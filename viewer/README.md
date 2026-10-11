# Red-team viewer (Google Apps Script web app)

A phone-friendly page for a sheets-llm sheet. People type a prompt number and see the
prompt with every model's answer as a tree of colour-coded cards (one column per model
on a laptop or projector, stacked on a phone). They can also submit prompts.

- **Lookup is per viewer.** The number lives in each person's URL (`?r=123`), so 20 people
  can look at 20 different prompts. Viewing never writes to the sheet.
- **Live.** While any model is still `PROCESSING` or queued, the page re-checks every 6 s.
- **Numbers = sheet rows.** Prompt #123 is row 123 of the `Prompts` tab, the row that
  sheets-llm answers in every model tab. If someone inserts or deletes rows after a model
  has run, that card is flagged "row shifted" instead of showing the wrong answer.
- **+ Ask** appends a row to `Prompts` and jumps to its number. The phone remembers its own
  numbers ("Yours: #123 #130").
- **Sheet ↗** in the header opens the Google Sheet itself (URL read from the bound sheet).
  Viewers can only open it if the sheet is shared as **Anyone with the link → Viewer**.
- Controls: number box + Enter, ◀ ▶, Latest, arrow keys, swipe left/right.

## Deploy (about 5 minutes)

1. Open the Google Sheet, then **Extensions → Apps Script**.
2. Replace `Code.gs` with this folder's `Code.gs`.
3. **+ (Add a file) → HTML**, name it `Index`, paste `Index.html`.
4. **Deploy → New deployment → Select type: Web app**.
   - Execute as: **Me**
   - Who has access: **Anyone**
5. Authorize when asked (Google warns that the app is unverified: Advanced → Go to project).
6. Copy the `https://script.google.com/macros/s/…/exec` URL. Shorten it and make the QR from that.

After editing the code, use **Deploy → Manage deployments → Edit → Version: New version**
so the same URL picks up the change.

Run sheets-llm in stream mode alongside it (see "Running a live session" in the main README):

```bash
python sheets_inference.py --sheet-url URL --config models.yaml --mode stream --poll-interval 2
```

## Custom domain

Apps Script web apps can't be served on a custom domain, so a CNAME straight to Google
won't work: Google won't answer for your hostname. You need a small redirect page that
does answer for it, and it must answer over **HTTPS**.

Namecheap's **URL Redirect Record** only answers on plain HTTP. Firefox falls back to HTTP
and works. Chrome tries `https://` first, the connection hangs on port 443, and you get
`ERR_CONNECTION_TIMED_OUT`, on phones in particular. Don't use it.

A GitHub Pages redirect works instead, and it is free with automatic HTTPS:

1. Create a public repo with an `index.html` that redirects to the `/exec` URL. Copy it to
   `404.html` so any path redirects too, and add a `CNAME` file containing the hostname:

   ```html
   <!doctype html>
   <meta charset="utf-8">
   <meta http-equiv="refresh" content="0; url=https://script.google.com/macros/s/…/exec">
   <script>location.replace("https://script.google.com/macros/s/…/exec");</script>
   <a href="https://script.google.com/macros/s/…/exec">Continue</a>
   ```

2. Turn on Pages for the repo and set the custom domain:

   ```bash
   gh api -X POST repos/OWNER/REPO/pages -f 'source[branch]=main' -f 'source[path]=/'
   gh api -X PUT  repos/OWNER/REPO/pages -f cname=app.example.com
   ```

3. In Namecheap, delete any URL Redirect, A or parking record for the host, then add
   **Advanced DNS → CNAME Record**: host `app`, value `OWNER.github.io.`.
4. Wait for the certificate (minutes to a few hours), then enforce HTTPS:

   ```bash
   gh api repos/OWNER/REPO/pages -q .https_certificate.state   # wait for "approved"
   gh api -X PUT repos/OWNER/REPO/pages -F https_enforced=true
   ```

   If the domain was set in Pages before DNS pointed at GitHub, the certificate can stall
   at `authorization_created`. Remove and re-add the custom domain once DNS resolves.
5. If Chrome cached the old HTTPS failure, clear it at `chrome://net-internals/#hsts` →
   "Delete domain security policies".

The class instance is `redteam.mmdanziger.com` → repo `mmdanziger/redteam-redirect`.
Redeploy the script by editing the existing deployment (new version), not by creating a new
deployment, so the `/exec` URL and the redirect page stay valid.

## Afterwards

Anyone with the link can read every prompt and add new ones. When the session is over,
**Deploy → Manage deployments → Archive**.
