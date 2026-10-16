#!/usr/bin/env python3
"""Experimental NovelCool 1.6.1 parser fix for keiyoushi/extensions-source.

Run from the repository root; modifies only NovelCool.kt and build.gradle.kts.
Does NOT produce a working JAR by itself.
"""
from pathlib import Path
import re

BASE = Path('src/all/novelcool')
SOURCE = BASE / 'src/eu/kanade/tachiyomi/extension/all/novelcool/NovelCool.kt'
GRADLE = BASE / 'build.gradle.kts'

replacement = r'''    override suspend fun getPageList(chapter: SChapter): List<Page> {
        var doc = pageClient.get(getChapterUrl(chapter)).asJsoup()

        // Some requests end on an intermediate source-selector page.
        doc.selectFirst("a.vision-button[href]")?.attr("abs:href")
            ?.takeIf { it.isNotBlank() }
            ?.let { target ->
                val forwardedHeaders = headers.newBuilder()
                    .set("Referer", doc.baseUri())
                    .build()
                doc = pageClient.get(target, forwardedHeaders).asJsoup()
            }

        // NovelCool has used both `all_imgs_url: [...]` and `all_imgs_url = [...]`.
        // Read every script instead of just the first script containing the name.
        val scripts = doc.select("script").joinToString("\n") { it.data() }
            .replace("\\/", "/")
        val imageArray = Regex("""all_imgs_url[\"']?\s*[:=]\s*\[([\s\S]*?)]""")
            .find(scripts)?.groupValues?.get(1)
        if (imageArray != null) {
            val urls = imageUrlRegex.findAll(imageArray)
                .map { it.groupValues[1] }
                .distinct()
                .toList()
            if (urls.isNotEmpty()) {
                return urls.mapIndexed { index, url -> Page(index, imageUrl = url) }
            }
        }

        // Fallback for chapters served as individual pages instead of an array.
        val pageUrls = doc.select(
            ".mangaread-pagenav select option[value], " +
                "select.sl-page option[value], " +
                "select[id*=page] option[value], select[name*=page] option[value]",
        ).mapNotNull { option ->
            val raw = option.attr("value").trim()
            if (!(raw.startsWith("/") || raw.startsWith("http") || raw.startsWith("?"))) {
                null
            } else {
                runCatching { doc.baseUri().toHttpUrl().resolve(raw)?.toString() }
                    .getOrNull()
                    ?.takeIf { it.contains("/chapter/") }
            }
        }.distinct()
        if (pageUrls.isNotEmpty()) {
            return pageUrls.mapIndexed { index, url -> Page(index, url = url) }
        }

        // If the page contains actual comic images directly, at least return them.
        val images = doc.select(
            "img.mangaread-manga-pic, #manga-reading img, .mangaread-content img, " +
                "img[src*=movietop.cc], img[data-src*=movietop.cc]",
        ).mapNotNull { element ->
            sequenceOf("data-src", "lazy_url", "src")
                .map { element.attr("abs:$it") }
                .firstOrNull { it.startsWith("http") }
        }.distinct()
        return images.mapIndexed { index, url -> Page(index, imageUrl = url) }
    }

    override suspend fun getImageUrl(page: Page): String {
        val doc = pageClient.get(page.url).asJsoup()
        return doc.selectFirst(
            "img.mangaread-manga-pic, #manga-reading img, .mangaread-content img, " +
                "img[src*=movietop.cc], img[data-src*=movietop.cc]",
        )?.let { element ->
            sequenceOf("data-src", "lazy_url", "src")
                .map { element.attr("abs:$it") }
                .firstOrNull { it.startsWith("http") }
        } ?: error("NovelCool: image not found in ${page.url}")
    }

'''

if not SOURCE.is_file() or not GRADLE.is_file():
    raise SystemExit('Missing NovelCool source. Run this from the keiyoushi/extensions-source root.')
source = SOURCE.read_text(encoding='utf-8')
start_marker = '    override suspend fun getPageList(chapter: SChapter): List<Page> {'
end_marker = '    private fun Elements.imgAttr(): String = when {'
if source.count(start_marker) != 1 or source.count(end_marker) != 1:
    raise SystemExit('Unexpected NovelCool.kt version; aborting safely.')
start = source.index(start_marker)
end = source.index(end_marker, start)
modified = source[:start] + replacement + source[end:]
if modified == source:
    raise SystemExit('Source did not change.')

gradle = GRADLE.read_text(encoding='utf-8')
changed, times = re.subn(r'\bversionCode\s*=\s*1\b', 'versionCode = 2', gradle)
if times != 1:
    raise SystemExit('Unexpected build.gradle.kts version; aborting safely.')
SOURCE.write_text(modified, encoding='utf-8')
GRADLE.write_text(changed, encoding='utf-8')
print('NovelCool.kt parser updated; version bumped to 1.6.2 (experimental).')
