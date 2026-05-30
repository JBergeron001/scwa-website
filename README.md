# SCWA Learning Portal — Website

**St. Charles Watersheds Alliance** — Professional Green Infrastructure Certification Platform

## Live site
Hosted via GitHub Pages at: `https://[your-github-username].github.io/scwa-website/`

## Pages

| File | URL | Purpose |
|------|-----|---------|
| `index.html` | `/` | Homepage — hero, cert overview, features, how it works |
| `about.html` | `/about.html` | About SCWA — mission, approach |
| `certifications.html` | `/certifications.html` | All 4 certifications detail + pricing |
| `enroll.html` | `/enroll.html` | Enrollment form — 4-step: details → cert → payment → confirmation |
| `portal.html` | `/portal.html` | Learning portal — sidebar nav, slide viewer, TTS, interactive slides |
| `contact.html` | `/contact.html` | Contact form |

## Structure

```
scwa-website/
├── index.html
├── about.html
├── certifications.html
├── enroll.html
├── portal.html
├── contact.html
├── css/
│   └── style.css          ← all shared styles, design tokens, components
├── js/
│   └── (future: slide data, TTS engine, progress tracking)
└── assets/
    └── (future: images, PDFs, slide images exported from PPTX)
```

## Design system

- **Fonts**: DM Serif Display (headings) + DM Sans (body) — loaded from Google Fonts
- **Icons**: Tabler Icons webfont — loaded from jsDelivr CDN
- **Colors**: Navy `#0D1B2A` · Green `#1D9E75` · Gold `#C9A84C` · Cream `#F8F6F1`
- **No build tools required** — pure HTML/CSS/JS, works directly as static files

## Deploying to GitHub Pages

### Step 1 — Create the repository
1. Go to [github.com/new](https://github.com/new)
2. Name it `scwa-website` (or any name)
3. Set to **Public** (required for free GitHub Pages)
4. Click **Create repository**

### Step 2 — Upload the files
**Option A — GitHub web interface (easiest):**
1. Open your new repository
2. Click **Add file → Upload files**
3. Drag all files and folders from this directory
4. Commit with message: `Initial site launch`

**Option B — Git command line:**
```bash
git init
git add .
git commit -m "Initial SCWA website"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/scwa-website.git
git push -u origin main
```

### Step 3 — Enable GitHub Pages
1. Go to your repository → **Settings → Pages**
2. Under **Source**, select **Deploy from a branch**
3. Branch: `main` · Folder: `/ (root)`
4. Click **Save**
5. Wait 2–5 minutes — your site will be live at `https://YOUR-USERNAME.github.io/scwa-website/`

### Step 4 — Custom domain (optional)
1. In repository Settings → Pages → Custom domain
2. Enter your domain (e.g. `certs.scwa.org`)
3. Add a CNAME record at your DNS provider pointing to `YOUR-USERNAME.github.io`

## Payment integration (production)

The enrollment form currently simulates payment. For production, replace the payment step with:

**Stripe (recommended):**
```html
<!-- Replace the card form in enroll.html step 3 with: -->
<script src="https://js.stripe.com/v3/"></script>
<!-- Then use Stripe Elements for PCI-compliant card input -->
```

Or use **Stripe Payment Links** — generate a payment link per certification in your Stripe dashboard and redirect users there after form completion.

## Adding slide content

Slide content is defined in `portal.html` inside the `SLIDES` JavaScript object. To add a certification's slides:

```javascript
SLIDES[moduleId] = [
  { type: 'intro', tag: 'Module 1', title: 'Your title', body: 'Body text', bullets: ['Point 1', 'Point 2'] },
  { type: 'content', tag: 'Module 1 · Slide 2', title: 'Title', body: 'Body', highlight: 'Code or formula', bullets: [] },
  { type: 'hotspot', tag: '...', title: '...', body: '...', hotspots: [{x:'20%',y:'40%',n:'1',title:'Label',desc:'Detail'}] },
  { type: 'links', tag: '...', title: '...', body: '...', links: [{icon:'ti-file-text',title:'Title',sub:'Subtitle',url:'https://...'}] },
  { type: 'quiz', tag: '...', title: '...', q: 'Question?', opts: ['A','B','C','D'], correct: 1, explanation: 'Explanation text.' }
]
```

## Text-to-speech

TTS uses the browser's native **Web Speech API** — no API key or third-party service required. Works in Chrome, Edge, Safari, Firefox. For production-quality voices (especially non-English), consider upgrading to:
- **Google Cloud Text-to-Speech API** — natural voices in 40+ languages
- **AWS Polly** — neural TTS with SSML support

## Future: user authentication & progress tracking

For real user accounts and persistent progress:
1. **Supabase** (free tier, PostgreSQL) — add auth + a `progress` table
2. **Firebase** — Google's auth + Firestore for real-time progress sync
3. **Netlify Identity** — if you migrate hosting to Netlify

Both are free for small-to-medium usage and integrate with static HTML sites via their JavaScript SDKs.
