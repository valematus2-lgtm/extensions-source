#!/usr/bin/env python3
"""Patch Keiyoushi NovelCool to handle page-by-page HTML and show useful errors.

Run from the root of a freshly checked out keiyoushi/extensions-source fork.
Requires no pip dependencies.
"""
from pathlib import Path
import re

BASE = Path('src/all/novelcool')
SOURCE = BASE / 'src/eu/kanade/tachiyomi/extension/all/novelcool/NovelCool.kt'
GRADLE = BASE / 'build.gradle.kts'

replacement = r'''    override suspend fun getPageList(chapter: SChapter): List<Page> {
        val chapterUrl = getChapterUrl(chapter)
        val browserHeaders = headers.newBuilder()
            .set("User-Agent", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
            .set("Referer", "$baseUrl/")
            .build()

        // NovelCool supports several chapter address forms. We try the ordinary
        // address first, followed by its explicit first-page variant.
        val candidateUrls = mutableListOf(chapterUrl)
        val withoutQuery = chapterUrl.substringBefore('?')
        if (withoutQuery.contains("/chapter/")) {
            val stem = withoutQuery.removeSuffix("/").removeSuffix(".html")
            if (!stem.endsWith("-1")) {
                candidateUrls.add("$stem-1.html")
                candidateUrls.add("$stem-1-1.html")
            }
        }

        var lastDiagnostic = "No requests completed"
        for (url in candidateUrls.distinct()) {
            val attempt = runCatching { client.get(url, browserHeaders) }
            val response = attempt.getOrNull()
            if (response == null) {
                lastDiagnostic = "Request failed for ${url.substringBefore('?')}: ${attempt.exceptionOrNull()?.message}"
                continue
            }
            var doc = response.asJsoup()
            parseChapterPages(doc)?.let { return it }

            // Some requests are sent to an intermediate source-selection page.
            // Only try this route if the actual chapter was not served directly.
            val serverUrl = doc.selectFirst("a.vision-button[href]")?.attr("abs:href")
            if (!serverUrl.isNullOrBlank()) {
                doc = runCatching {
                    val nextHeaders = browserHeaders.newBuilder()
                        .set("Referer", doc.baseUri())
                        .build()
                    pageClient.get(serverUrl, nextHeaders).asJsoup()
                }.getOrElse { doc }
                parseChapterPages(doc)?.let { return it }
            }
            lastDiagnostic = "requested=${url.substringBefore('?')}, " +
                "landed=${doc.baseUri().substringBefore('?')}, " +
                "title=${doc.title().take(60)}, " +
                "pageOptions=${doc.select("select.sl-page option").size}, " +
                "images=${doc.select("img.mangaread-manga-pic").size}"
        }
        error("NovelCool Fix: no pages. $lastDiagnostic")
    }

    private fun parseChapterPages(doc: Document): List<Page>? {
        // Layout A: one JS array containing every image URL.
        val scripts = doc.select("script").joinToString("\n") { it.data() }
            .replace("\\/", "/")
        val imageArray = Regex("""all_imgs_url["']?\s*[:=]\s*\[([\s\S]*?)]""")
            .find(scripts)?.groupValues?.get(1)
        if (imageArray != null) {
            val urls = imageUrlRegex.findAll(imageArray)
                .map { it.groupValues[1].replace("\\/", "/") }
                .distinct().toList()
            if (urls.isNotEmpty()) return urls.mapIndexed { i, u -> Page(i, imageUrl = u) }
        }

        // Layout B (verified against The Chain of Youth, chapter 1):
        // 21 options in <select class="sl-page">, one image per HTML page.
        val pageSelect = doc.selectFirst("select.sl-page")
        val pageUrls = pageSelect?.select("option[value]")?.mapNotNull { option ->
            val raw = option.attr("value").trim()
            val resolved = runCatching { doc.baseUri().toHttpUrl().resolve(raw) }.getOrNull()
            resolved?.toString()?.takeIf { it.contains("/chapter/") }
        }?.distinct().orEmpty()
        if (pageUrls.isNotEmpty()) {
            return pageUrls.mapIndexed { i, u -> Page(i, url = u) }
        }

        // Layout C: images rendered directly, without a page selector.
        val directImages = doc.select("#viewer img.mangaread-manga-pic, img.mangaread-manga-pic")
            .mapNotNull { image ->
                listOf("data-src", "lazy_url", "src")
                    .map { image.attr("abs:$it") }
                    .firstOrNull { it.startsWith("https://") || it.startsWith("http://") }
            }.distinct()
        if (directImages.isNotEmpty()) {
            return directImages.mapIndexed { i, u -> Page(i, imageUrl = u) }
        }
        return null
    }

    override suspend fun getImageUrl(page: Page): String {
        val requestHeaders = headers.newBuilder()
            .set("User-Agent", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
            .set("Referer", "$baseUrl/")
            .build()
        val doc = client.get(page.url, requestHeaders).asJsoup()
        val image = doc.selectFirst("img.mangaread-manga-pic")
            ?: error("NovelCool Fix: page image missing at ${page.url.substringBefore('?')}; title=${doc.title()}")
        return listOf("data-src", "lazy_url", "src")
            .map { image.attr("abs:$it") }
            .firstOrNull { it.startsWith("http://") || it.startsWith("https://") }
            ?: error("NovelCool Fix: image source empty at ${page.url.substringBefore('?')}")
    }

'''
# We deliberately replace the whole original reader, but leave the rest of the
# upstream extension intact (search, list, metadata, language, source IDs).
if not SOURCE.is_file() or not GRADLE.is_file():
    raise SystemExit('Open the root of the keiyoushi/extensions-source fork first.')
source = SOURCE.read_text(encoding='utf-8')
start_marker = '    override suspend fun getPageList(chapter: SChapter): List<Page> {'
end_marker = '    private fun Elements.imgAttr(): String = when {'
if source.count(start_marker) != 1 or source.count(end_marker) != 1:
    raise SystemExit('Unexpected source: not patching NovelCool.kt.')
start = source.index(start_marker)
end = source.index(end_marker, start)
updated = source[:start] + replacement + source[end:]

# Increase extension version so Tachimanga sees this as newer than 1.6.2.
gradle = GRADLE.read_text(encoding='utf-8')
new_gradle, count = re.subn(r'\bversionCode\s*=\s*1\b', 'versionCode = 3', gradle)
if count != 1:
    raise SystemExit('Unexpected NovelCool versionCode; not patching.')
SOURCE.write_text(updated, encoding='utf-8')
GRADLE.write_text(new_gradle, encoding='utf-8')
print('Patched NovelCool version 1.6.3 with chapter parser and diagnostics.')
