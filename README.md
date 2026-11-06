# Lumad Lingua — Mansaka Sentiment Validation Studio

A working, single-administrator web application for reviewing **175 preprocessed Mansaka Facebook candidate posts** and collecting **human-validated Positive / Negative / Neutral** labels. All original CSV fields are preserved. No accounts or registration are required.

## Option A — Open immediately (offline copy)

Open `Lumad_Lingua_Validator_Offline.html` in Chrome, Edge, or Firefox. You can review and save the posts without installing anything. This copy stores annotations in **that one browser's local storage only** — it does **not** synchronize between devices and is **not password-protected**. Export the **complete CSV** regularly as a backup. Do not publish the offline file: it contains the dataset embedded inside it.

For a private link accessible to an administrator across devices, use Option B.

## Option B — Run the private website (recommended for actual research)

**Requirements:** Python 3.10 or later. No third-party packages or database setup are needed.

1. Unzip `Lumad_Lingua_Validator_Website.zip`.
2. Open the extracted `Lumad_Lingua_Validator` folder.
3. Double-click `start_windows.bat` on Windows (Python must be installed), or run `python server.py` in Terminal. On Mac/Linux, `bash start_mac_linux.sh` also works.
4. Copy the **generated temporary password printed in the terminal**.
5. Visit **http://127.0.0.1:8000** in your browser and enter the password.

This local website keeps saved data in `.data/annotations.sqlite3`. Restarting the server does **not** erase annotations. The automatically generated local password changes each time you restart the server; your annotations do not change.

To use a persistent password, set an environment variable first:

Windows PowerShell:

```powershell
$env:LL_ADMIN_PASSWORD="your-unique-password-of-at-least-12-characters"
python server.py
```

Mac/Linux:

```bash
LL_ADMIN_PASSWORD='your-unique-password-of-at-least-12-characters' python3 server.py
```

**Do not share the password publicly.** This is one shared admin password, not individual user accounts.

## Website sections

- **Overview:** progress, training-ready rows, pending/excluded/uncertain, and a distribution chart based on **human labels only**.
- **Validate posts:** one post at a time. Select language (`Mansaka`, `Mixed`, `Not Mansaka`, `Not sure`), then select sentiment (`Positive`, `Negative`, `Neutral`) for eligible posts. Notes are optional. Save and next, save and stay, skip, and revise earlier annotations.
- **Post library:** search, status and review-priority filters, click to edit any row.
- **Data & export:** download training-ready CSV, complete dataset with human annotations, full revision history, or import another preprocessed CSV. Imports skip duplicate post IDs.

**Keyboard shortcuts:** While on the review page, after selecting Mansaka or Mixed Mansaka, press `1` for Positive, `2` for Negative, or `3` for Neutral. `Ctrl+Enter` or `Cmd+Enter` submits and moves to the next post (when the focus is not inside a text field).

AI-reviewed sentiment is shown **only after a completed human sentiment validation**, to reduce anchoring bias. AI outputs never become final labels automatically.

### Sentiment guidelines

- **Positive:** explicitly favorable sentiment, joy, praise, affection, approval, gratitude or celebration.
- **Negative:** explicitly unfavorable sentiment, sadness, frustration, anger, rejection, disappointment, criticism, or fear.
- **Neutral:** primarily factual content or no clear positive or negative evaluation. Consider the whole message and its cultural context rather than isolated words.

A greeting is **not automatically positive**. A complaint that mentions a happy event is **not automatically positive**. Use **Not sure** for uncertain language identity and **Skip** for a post that requires another look.

## Exports and Google Colab

**Training-ready dataset**: columns `research_post_id`, `cleaned_text`, `anonymized_text`, `sentiment_label`, `language_verified`, `validated_at`. Includes only records with human language judgment `mansaka` or `mixed` **and** human sentiment Positive / Negative / Neutral.

**Complete dataset**: all original input fields plus `human_language_label`, `human_sentiment_label`, `validation_notes`, `human_review_status`, `validated_at`, `review_revision`. Includes all posts, including pending and excluded.

**Revision history**: one row for every time a human submission was saved, including changes to a post's previous label.

Do **not** treat `ai_reviewed_sentiment` or provisional labels as ground truth. With one shared administrator, this data is **single-annotator** and cannot establish inter-annotator reliability. Keep the separate revision export for auditability.

## Deploy as a private web link

This Python standard-library server can be deployed to a hosting provider that supports **Python or Docker and persistent storage** (e.g. a paid persistent disk or mounted volume). Deployment is **not automatic** and is **not included** in this package.

Required environment configuration:

| Name | Meaning |
|---|---|
| `LL_HOST=0.0.0.0` | Listen on all interfaces for hosted deployment |
| `LL_ADMIN_PASSWORD=<long,unique password>` | Mandatory when hosting publicly; minimum 12 characters |
| `PORT=<provider port>` | Automatically used if provider supplies it; else `LL_PORT` or 8000 |
| `LL_DATA_DIR=/path/to/persistent-volume` | Directory for persistent SQLite database |
| `LL_SECURE_COOKIES=1` | Require secure cookies behind HTTPS reverse proxy (recommended) |

Start command: `python server.py`.

**Before deploying:**

1. Attach a **persistent volume/disk** and set `LL_DATA_DIR` to its mounted path. Hosts with ephemeral disks will erase reviews on restart or redeploy.
2. Use HTTPS. Set `LL_SECURE_COOKIES=1`. Use a strong shared password, ideally managed as a secret environment variable.
3. Share only the private HTTPS site address and password with the authorized administrator; do not publish the offline HTML or raw CSV.
4. Back up the `.data` directory periodically. Also download the full reviewed CSV and history at milestones.
5. Ensure your collection/use of Facebook posts complies with applicable privacy obligations and research consent requirements. The original data indicates automated PII redaction, **not confirmed manual privacy review**.

`Dockerfile` is provided for platforms that deploy containers. Example local Docker run:

```bash
docker build -t lumad-validator .
docker run --rm -p 8000:8000 -v lumad_annotations:/app/.data \
  -e LL_HOST=0.0.0.0 -e LL_ADMIN_PASSWORD='change-this-to-a-secret-phrase' \
  lumad-validator
```

## Data persistence and security notes

- Annotation storage uses SQLite (transactions and a history table) on the **server**.
- A shared password protects all API endpoints except login and session; HttpOnly, SameSite cookies and a custom request header protect update requests.
- This version is a compact, one-admin research tool, **not a fully audited production identity provider**. Host only behind HTTPS and keep the password secret.
- Imported CSV data is not automatically anonymized. Review it before uploading.
- The bundled seed CSV includes all **175 unique research IDs**, their original anonymized text, cleaned text, and metadata. None are pre-labeled as human-validated.

## Project structure

```text
Lumad_Lingua_Validator/
  server.py               # Python web server, auth, SQLite, CSV API
  static/
    index.html            # Admin UI
    style.css             # Responsive design
    app.js                # Interactive validation UI
    favicon.svg
  data/
    mansaka_candidates.csv # 175 supplied candidate posts
  start_windows.bat
  start_mac_linux.sh
  Dockerfile
  README.md
  build_offline.py        # Optional builder for standalone offline HTML
```

Created for the Lumad Lingua Mansaka research project. Version 1.0.
