# moodle-course-exporter

Export a [Moodle](https://moodle.org/) course into a tidy folder tree of
**files + Markdown**, for offline reading, backup, or importing into a note app
like [Obsidian](https://obsidian.md/).

- **Files** (PDF, slides, archives, Jupyter notebooks, source code, ...) are
  downloaded as-is.
- **Pages, forums, assignments, books and section descriptions** are converted
  to Markdown with YAML front matter.
- The original **section structure** of the course is preserved as numbered
  folders.

Authentication uses cookies from a browser session you are already logged into,
so it works even with **university single sign-on** (Shibboleth / Keycloak /
ADFS / ...). Your password is never handed to the script.

## Install

Requires Python 3.7+.

```bash
git clone https://github.com/<you>/moodle-course-exporter.git
cd moodle-course-exporter
pip3 install -r requirements.txt
```

## Authentication: get your session cookie

The script needs the cookies of a logged-in session. Pick whichever is easier.

### Option A — Chrome / Firefox extension (simplest)

1. Log in to your Moodle in the browser.
2. Install a cookies exporter extension that produces the **Netscape
   `cookies.txt`** format (e.g. *“Get cookies.txt LOCALLY”* for Chrome,
   *“cookies.txt”* for Firefox).
3. On the course page, export cookies for your Moodle domain and save the file
   as `cookies.txt`.
4. Run with `--cookies cookies.txt`.

### Option B — Safari / any browser, no extension

The important cookie is `MoodleSession…`, and it is **HttpOnly** (so it does
*not* show up in `document.cookie`). Read it from the dev tools:

1. Safari → **Settings → Advanced → “Show features for web developers”**.
2. Open your Moodle (logged in) → **Develop → Show Web Inspector** (`⌥⌘I`).
3. **Storage → Cookies →** your Moodle domain.
4. Copy the row whose **name** starts with `MoodleSession`.
5. Put `MoodleSession=<value>` into a file, e.g. `session.txt`, and run with
   `--cookie-file session.txt`. (Chrome/Edge/Firefox have the same panel under
   *Application/Storage → Cookies*.)

> The session cookie is a live access token — keep it private and delete it
> when you are done. `cookies.txt` / `session.txt` are git-ignored by default.

## Usage

```bash
python3 moodle_export.py \
  --base-url https://moodle.example.edu \
  --course  "https://moodle.example.edu/course/view.php?id=1234" \
  --cookie-file session.txt \
  --out      ./export
```

Cookie input — pick one:

| Flag | Source |
| --- | --- |
| `--cookies cookies.txt` | Netscape `cookies.txt` from a browser extension |
| `--cookie-file FILE` | a file containing a raw `name=value; ...` string |
| `--cookie "MoodleSession=..."` | the raw cookie string inline |

Other options:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--out DIR` | `./export` | output directory |
| `--delay SECONDS` | `0.5` | pause between requests (be polite to the server) |

The `--course` URL is the normal course page from your browser
(`.../course/view.php?id=<number>`).

## Output layout

```
export/
└── <Course title>/
    ├── 01_<Section>/
    │   ├── _overview.md        # section description / labels (links, notes)
    │   ├── slides.pdf
    │   └── page-content.md
    ├── 02_<Section>/
    │   ├── assignment.md       # description + due dates
    │   └── assignment/         # attached files
    └── ...
```

Each generated Markdown file has front matter (`title`, `source`, `type`) so
it indexes cleanly in Obsidian and similar tools.

## Supported activity types

Dedicated handlers: `resource` (files), `folder`, `page`, `url`, `assign`,
`forum`, `book`, `moodleoverflow`.

Generic handler (saves description + external links, downloads any attachments):
`lti`, `extserver`, `zoom`, `organizer`, `quiz`, `choice`, `feedback`,
`glossary`, `wiki`, `lesson`, `workshop`, `wordcloud`.

Unknown types are reported and skipped. Moodle themes vary between
installations, so if something is missed on your site, the CSS selectors in the
handler functions are the place to adjust.

## Notes & limitations

- Only content **your account can already access** is exported — this is not a
  way around permissions.
- Activities backed by external services (Zoom, LTI tools, external submission
  servers) can only be captured as a description + link; their content lives on
  the third party.
- Identical files linked multiple times within one section are de-duplicated;
  copies in different sections are kept.

## ⚠️ Responsible use

Course materials (lecture slides, problem sets, recordings, ...) are typically
**copyrighted** by their authors or institution. Use this tool for **personal,
offline study and backup** of material you are entitled to access. Do **not**
redistribute or publish downloaded course content (e.g. in a public
repository) without permission, and follow your institution's policies and
local law.

## License

[MIT](LICENSE)
