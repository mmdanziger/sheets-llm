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

## Custom domain (e.g. Namecheap)

Apps Script web apps can't be served on a custom domain, so A and CNAME records won't work
(Google won't answer for your hostname). Use a redirect instead. In Namecheap,
**Advanced DNS → Add New Record → URL Redirect Record**:

| Host | Value | Type |
|------|-------|------|
| `@` or a subdomain such as `app` | the `…/exec` URL | Permanent (301) |

- Don't use **Masked**: it wraps the page in an iframe, which Apps Script blocks by default.
- Remove any existing A/CNAME/parking record for the same host first.
- Redeploy by editing the existing deployment (new version), not with a new deployment,
  so the `/exec` URL and the redirect stay valid.

## Afterwards

Anyone with the link can read every prompt and add new ones. When the session is over,
**Deploy → Manage deployments → Archive**.
