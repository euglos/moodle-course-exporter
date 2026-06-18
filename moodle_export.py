#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Export a Moodle course into a folder tree of files + Markdown.

Authentication uses cookies from an already logged-in browser session, so it
works even with university SSO (Shibboleth / Keycloak / ADFS). Your password
is never given to the script.

Files (PDF, slides, archives, notebooks, ...) are downloaded as-is. Pages,
forums, assignments, books and section descriptions are converted to Markdown
with YAML front matter (handy for Obsidian and other note apps).

Usage:
    python3 moodle_export.py \
        --base-url https://moodle.example.edu \
        --course "https://moodle.example.edu/course/view.php?id=1234" \
        --cookie-file session.txt \
        --out ./export

Cookie input (pick one):
    --cookies cookies.txt   Netscape cookies.txt exported from Chrome/Firefox
    --cookie-file FILE      file containing a raw "name=value; ..." cookie string
    --cookie "MoodleSession=..."   the raw cookie string inline

Dependencies: requests, beautifulsoup4, markdownify
    pip3 install -r requirements.txt
"""

import argparse
import hashlib
import http.cookiejar
import os
import re
import sys
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from markdownify import markdownify as md
except ImportError:
    md = None


# ----------------------------- helpers -----------------------------

INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Trailing module-type words Moodle appends to activity names, in several
# languages (English/German/Russian). Stripped so file names stay clean.
TYPE_SUFFIX = re.compile(
    r"\s*(File|Page|URL|Folder|Assignment|Forum|Resource|Book|Test|"
    r"External tool|Datei|Textseite|Aufgabe|Verzeichnis|Ordner|Buch|"
    r"Externes Tool|Файл|Страница|Папка|Задание|Форум|Ссылка)\s*$"
)


def safe_name(name, maxlen=120):
    """Turn an arbitrary string into a filesystem-safe file/folder name."""
    name = (name or "").strip()
    name = INVALID.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if len(name) > maxlen:
        name = name[:maxlen].rstrip()
    return name or "untitled"


def to_markdown(html):
    """Convert an HTML fragment to Markdown (uses markdownify if available)."""
    if not html:
        return ""
    if md:
        return md(str(html), heading_style="ATX", strip=["script", "style"]).strip()
    # fallback: plain text extraction
    return BeautifulSoup(str(html), "html.parser").get_text("\n", strip=True)


def unique_path(path):
    """If a file already exists, append _2, _3, ... so nothing is overwritten."""
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 2
    while os.path.exists("{}_{}{}".format(root, i, ext)):
        i += 1
    return "{}_{}{}".format(root, i, ext)


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    path = unique_path(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print("  [md]  ", os.path.relpath(path))


def frontmatter(title, url, extra=None):
    lines = ["---", "title: " + (title or "").replace("\n", " ")]
    if url:
        lines.append("source: " + url)
    for k, v in (extra or {}).items():
        lines.append("{}: {}".format(k, str(v).replace("\n", " ")))
    lines.append("---\n\n")
    return "\n".join(lines)


# ----------------------------- session -----------------------------

def make_session(base_url, cookies_file=None, cookie_str=None):
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                       "Version/17.0 Safari/605.1.15"),
    })
    if cookie_str:
        # raw "name=value; name2=value2" string (as seen in browser devtools)
        domain = urlparse(base_url).hostname
        for part in cookie_str.split(";"):
            if "=" not in part:
                continue
            name, value = part.strip().split("=", 1)
            s.cookies.set(name.strip(), value.strip(), domain=domain, path="/")
    elif cookies_file:
        jar = http.cookiejar.MozillaCookieJar(cookies_file)
        jar.load(ignore_discard=True, ignore_expires=True)
        s.cookies = jar
    return s


def check_login(session, base_url):
    """Best-effort check that the cookies actually authenticate us."""
    r = session.get(base_url, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")
    # a logged-in page usually exposes a logout link
    if soup.find("a", href=re.compile(r"login/logout")):
        return True
    body = soup.find("body")
    classes = (body.get("class", []) if body else [])
    return "userloggedin" in classes


# ----------------------------- course parsing -----------------------------

def get_soup(session, url):
    r = session.get(url, timeout=60)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def module_type(href):
    """Extract the activity type from a /mod/<type>/view.php link."""
    m = re.search(r"/mod/([a-z]+)/view\.php", href)
    return m.group(1) if m else None


def parse_course(session, course_url):
    """Return (course_title, sections) where each section is
    {title, activities: [{name, url, type}], intro_html}."""
    soup = get_soup(session, course_url)

    course_title = ""
    h = soup.find(["h1"])
    if h:
        course_title = h.get_text(strip=True)

    sections = []
    seen_sec = set()
    for sec in soup.select("li.section, div.section, li[id^=section-]"):
        if id(sec) in seen_sec:      # same node may match several selectors
            continue
        seen_sec.add(id(sec))

        name_el = sec.select_one(".sectionname, h3.sectionname, .section-title")
        sec_title = name_el.get_text(strip=True) if name_el else ""

        # section description + inline "labels" (text without their own page)
        intro_chunks = []
        summ = sec.select_one(".summary, .summarytext")
        if summ and summ.get_text(strip=True):
            intro_chunks.append(str(summ))
        for lab in sec.select("li.modtype_label, li.activity.label, "
                              ".activity.modtype_label"):
            content = lab.select_one(".contentwithoutlink, .activity-altcontent, "
                                     ".no-overflow, .description") or lab
            if content and content.get_text(strip=True):
                intro_chunks.append(str(content))

        activities = []
        seen_act = set()
        for act in sec.select("li.activity"):
            link = act.select_one("a.aalink, a.activityinstance, a")
            if not link or not link.get("href"):
                continue
            href = urljoin(course_url, link["href"])
            mtype = module_type(href)
            if not mtype:
                continue
            if href in seen_act:          # same activity may appear twice
                continue
            seen_act.add(href)
            name_span = act.select_one(".instancename")
            name = (name_span.get_text(strip=True) if name_span
                    else link.get_text(strip=True))
            name = TYPE_SUFFIX.sub("", name)
            activities.append({"name": name.strip(), "url": href, "type": mtype})

        if sec_title or activities or intro_chunks:
            sections.append({"title": sec_title, "activities": activities,
                             "intro_html": "\n".join(intro_chunks)})

    return course_title, sections


# ----------------------------- activity handlers -----------------------------

def filename_from_response(resp, fallback):
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
    if m:
        return safe_name(requests.utils.unquote(m.group(1)))
    path = urlparse(resp.url).path
    base = os.path.basename(path)
    if base and "." in base:
        return safe_name(requests.utils.unquote(base))
    return fallback


def download_file(session, url, out_dir, fallback_name):
    r = session.get(url, stream=True, timeout=120, allow_redirects=True)
    r.raise_for_status()
    ctype = r.headers.get("Content-Type", "")

    # an HTML response is an intermediate page; find the real file link
    if "text/html" in ctype:
        soup = BeautifulSoup(r.content, "html.parser")
        real = None
        # explicit Moodle "workaround" block first
        link = soup.select_one(".resourceworkaround a, .urlworkaround a, "
                               "object[data], iframe[src]")
        if link:
            real = link.get("href") or link.get("data") or link.get("src")
        # otherwise any embedded file (a/img/embed/source/object), skipping theme assets
        if not real:
            for tag in soup.find_all(["a", "img", "object", "iframe", "embed", "source"]):
                v = tag.get("href") or tag.get("src") or tag.get("data")
                if v and "pluginfile.php" in v and "theme_" not in v:
                    real = v
                    break
        if real:
            return download_file(session, urljoin(url, real), out_dir, fallback_name)
        return None  # a real page, not a file

    fname = filename_from_response(r, fallback_name)
    os.makedirs(out_dir, exist_ok=True)
    path = unique_path(os.path.join(out_dir, fname))
    h = hashlib.sha1()
    with open(path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
            h.update(chunk)
    # de-duplicate by content within a single folder (the same file is often
    # linked several times in one section). Copies across sections are kept.
    seen = getattr(session, "_hashes", None)
    if seen is None:
        seen = session._hashes = {}
    key = (os.path.abspath(out_dir), h.hexdigest())
    if key in seen:
        os.remove(path)
        return seen[key]
    seen[key] = path
    print("  [file]", os.path.relpath(path))
    return path


def handle_resource(session, act, out_dir):
    download_file(session, act["url"], out_dir, safe_name(act["name"]))


def handle_folder(session, act, out_dir):
    soup = get_soup(session, act["url"])
    folder_dir = os.path.join(out_dir, safe_name(act["name"]))
    for a in soup.select('a[href*="pluginfile.php"]'):
        download_file(session, urljoin(act["url"], a["href"]), folder_dir,
                      safe_name(a.get_text(strip=True) or "file"))


def handle_page(session, act, out_dir):
    soup = get_soup(session, act["url"])
    content = soup.select_one("[role=main] .box.generalbox, "
                              "#region-main .no-overflow, #region-main .box, "
                              "[role=main]")
    body = to_markdown(content) if content else ""
    fm = frontmatter(act["name"], act["url"], {"type": "page"})
    write_text(os.path.join(out_dir, safe_name(act["name"]) + ".md"),
               fm + "# " + act["name"] + "\n\n" + body + "\n")


def handle_url(session, act, out_dir):
    soup = get_soup(session, act["url"])
    a = soup.find("a", href=re.compile(r"^https?://"))
    target = a["href"] if a else act["url"]
    fm = frontmatter(act["name"], act["url"], {"type": "url"})
    write_text(os.path.join(out_dir, safe_name(act["name"]) + ".md"),
               fm + "# " + act["name"] + "\n\n[" + target + "](" + target + ")\n")


def handle_assign(session, act, out_dir):
    soup = get_soup(session, act["url"])
    intro = soup.select_one("#intro, .box.generalbox, [role=main] .box")
    body = to_markdown(intro) if intro else ""
    # due dates and status from the summary table
    meta_lines = []
    for row in soup.select(".submissionsummarytable tr, table.generaltable tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]
        if len(cells) >= 2:
            meta_lines.append("- **{}:** {}".format(cells[0], cells[1]))
    sub_dir = os.path.join(out_dir, safe_name(act["name"]))
    # attached files
    for a in soup.select('[role=main] a[href*="pluginfile.php"]'):
        download_file(session, urljoin(act["url"], a["href"]), sub_dir,
                      safe_name(a.get_text(strip=True) or "attachment"))
    fm = frontmatter(act["name"], act["url"], {"type": "assignment"})
    text = fm + "# " + act["name"] + "\n\n"
    if meta_lines:
        text += "## Details\n" + "\n".join(meta_lines) + "\n\n"
    text += "## Description\n\n" + body + "\n"
    write_text(os.path.join(out_dir, safe_name(act["name"]) + ".md"), text)


def handle_forum(session, act, out_dir):
    soup = get_soup(session, act["url"])
    lines = [frontmatter(act["name"], act["url"], {"type": "forum"}),
             "# " + act["name"] + "\n"]
    for row in soup.select("table.forumheaderlist tr.discussion, "
                          ".discussion-list a"):
        link = row.find("a", href=True) if hasattr(row, "find") else None
        if not link:
            continue
        disc_url = urljoin(act["url"], link["href"])
        try:
            d = get_soup(session, disc_url)
        except Exception:
            continue
        lines.append("\n## " + link.get_text(strip=True) + "\n")
        for post in d.select(".forumpost, [data-region=post]"):
            subj = post.select_one(".subject, h3, .header-subject")
            cont = post.select_one(".posting, .post-content-container, .content")
            if subj:
                lines.append("\n### " + subj.get_text(strip=True))
            if cont:
                lines.append(to_markdown(cont))
        time.sleep(0.3)
    write_text(os.path.join(out_dir, safe_name(act["name"]) + ".md"),
               "\n".join(lines) + "\n")


def handle_book(session, act, out_dir):
    """Moodle book: concatenate all chapters into one Markdown file."""
    soup = get_soup(session, act["url"])
    parts = [frontmatter(act["name"], act["url"], {"type": "book"}),
             "# " + act["name"] + "\n"]
    chapter_urls = []
    for a in soup.select(".book_toc a, nav.booktoc a, .booktoc a"):
        if a.get("href"):
            u = urljoin(act["url"], a["href"])
            if u not in chapter_urls:
                chapter_urls.append(u)
    if not chapter_urls:
        chapter_urls = [act["url"]]
    for u in chapter_urls:
        try:
            d = get_soup(session, u)
        except Exception:
            continue
        cont = d.select_one(".book_content, [role=main] .box.generalbox, "
                            "#region-main .no-overflow")
        ttl = d.select_one(".book_content h2, [role=main] h2, h3")
        if ttl:
            parts.append("\n## " + ttl.get_text(strip=True))
        parts.append(to_markdown(cont) if cont else "")
        time.sleep(0.3)
    write_text(os.path.join(out_dir, safe_name(act["name"]) + ".md"),
               "\n".join(parts) + "\n")


def handle_moodleoverflow(session, act, out_dir):
    """moodleoverflow Q&A forum plugin: discussions + posts to Markdown."""
    soup = get_soup(session, act["url"])
    lines = [frontmatter(act["name"], act["url"], {"type": "forum"}),
             "# " + act["name"] + "\n"]
    disc_urls = []
    for a in soup.select('a[href*="discussion.php"]'):
        u = urljoin(act["url"], a["href"])
        if u not in disc_urls:
            disc_urls.append(u)
            lines.append("\n## " + a.get_text(strip=True))
            try:
                d = get_soup(session, u)
            except Exception:
                continue
            for post in d.select(".moodleoverflowpost, [class*=post], "
                                "[role=main] .box"):
                cont = post.select_one(".message, .content, .posting") or post
                txt = to_markdown(cont)
                if txt.strip():
                    lines.append(txt)
            time.sleep(0.3)
    write_text(os.path.join(out_dir, safe_name(act["name"]) + ".md"),
               "\n".join(lines) + "\n")


def handle_generic(session, act, out_dir):
    """External / interactive modules (lti, zoom, organizer, quiz, ...):
    save the description and external links to Markdown, plus any attachments."""
    soup = get_soup(session, act["url"])
    main = soup.select_one("[role=main], #region-main")
    body = to_markdown(main) if main else ""
    # external links (pointing away from this Moodle host)
    ext = []
    host = urlparse(act["url"]).hostname
    for a in (main.select("a[href]") if main else []):
        href = a["href"]
        if href.startswith("http") and host not in href:
            ext.append((a.get_text(strip=True) or href, href))
    fm = frontmatter(act["name"], act["url"], {"type": act["type"]})
    text = fm + "# " + act["name"] + "\n\n"
    text += ("> Moodle module type: `{}` (external/interactive — the actual "
             "content may live on a third-party service)\n\n").format(act["type"])
    if ext:
        text += "## Links\n" + "\n".join(
            "- [{}]({})".format(t, u) for t, u in dict(ext).items()) + "\n\n"
    text += body + "\n"
    write_text(os.path.join(out_dir, safe_name(act["name"]) + ".md"), text)
    # download any attached files present on the page
    for a in (main.select('a[href*="pluginfile.php"]') if main else []):
        try:
            download_file(session, urljoin(act["url"], a["href"]),
                          os.path.join(out_dir, safe_name(act["name"])),
                          safe_name(a.get_text(strip=True) or "file"))
        except Exception:
            pass


HANDLERS = {
    "resource": handle_resource,
    "folder": handle_folder,
    "page": handle_page,
    "url": handle_url,
    "assign": handle_assign,
    "forum": handle_forum,
    "book": handle_book,
    "moodleoverflow": handle_moodleoverflow,
}

# module types routed to the generic handler
GENERIC_TYPES = {"lti", "extserver", "zoom", "organizer", "wordcloud", "quiz",
                 "choice", "feedback", "glossary", "wiki", "lesson", "workshop"}


# ----------------------------- main -----------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Export a Moodle course into files + Markdown.")
    ap.add_argument("--base-url", required=True,
                    help="Moodle root, e.g. https://moodle.example.edu")
    ap.add_argument("--course", required=True,
                    help="course URL (.../course/view.php?id=...)")
    ap.add_argument("--cookies",
                    help="path to a Netscape cookies.txt (from Chrome/Firefox)")
    ap.add_argument("--cookie-file",
                    help="file containing a raw 'name=value; ...' cookie string")
    ap.add_argument("--cookie",
                    help='raw cookie string inline, e.g. "MoodleSession=..."')
    ap.add_argument("--out", default="./export", help="output directory")
    ap.add_argument("--delay", type=float, default=0.5,
                    help="delay between requests, seconds")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")

    cookie_str = args.cookie
    if args.cookie_file:
        with open(args.cookie_file, encoding="utf-8") as f:
            cookie_str = f.read().strip()

    if not cookie_str and not (args.cookies and os.path.exists(args.cookies)):
        sys.exit("error: provide cookies via --cookies, --cookie-file or --cookie")

    session = make_session(base, cookies_file=args.cookies, cookie_str=cookie_str)

    print("Checking login...")
    if check_login(session, base):
        print("  logged in.")
    else:
        print("  warning: not logged in (cookies missing/expired). "
              "Re-export cookies after logging in. Continuing anyway...")

    print("Reading course page...")
    course_title, sections = parse_course(session, args.course)
    course_dir = os.path.join(args.out, safe_name(course_title or "course"))
    print('  course: "{}" — {} sections'.format(course_title, len(sections)))

    for i, sec in enumerate(sections, 1):
        sec_name = "{:02d}_{}".format(i, safe_name(sec["title"] or "Section"))
        sec_dir = os.path.join(course_dir, sec_name)
        intro = sec.get("intro_html", "")
        if not sec["activities"] and not intro:
            continue
        print("\n[{}] {} activities".format(sec_name, len(sec["activities"])))
        # section description / labels -> _overview.md
        if intro:
            md_intro = to_markdown(intro)
            if md_intro.strip():
                write_text(os.path.join(sec_dir, "_overview.md"),
                           frontmatter(sec["title"] or sec_name, args.course,
                                       {"type": "section-intro"})
                           + "# " + (sec["title"] or sec_name) + "\n\n"
                           + md_intro + "\n")
        for act in sec["activities"]:
            handler = HANDLERS.get(act["type"])
            if not handler and act["type"] in GENERIC_TYPES:
                handler = handle_generic
            if not handler:
                print("  [skip] type '{}': {}".format(act["type"], act["name"]))
                continue
            try:
                handler(session, act, sec_dir)
            except Exception as e:
                print("  [warn] '{}': {}".format(act["name"], e))
            time.sleep(args.delay)

    print("\nDone. Course saved to:", course_dir)


if __name__ == "__main__":
    main()
