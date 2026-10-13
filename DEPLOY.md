# Putting RecallIndia online (https)

Hosting gives you a public `https://…` link. That's what makes the live camera and phone notifications work on phones, and it's a link you can put on your resume. The steps below use **Render** (free tier) with the `render.yaml` and `Dockerfile` in this repo.

## 1. Push the code to GitHub

```powershell
git push -u origin mvp-overview-alignment
```

Then open a pull request on GitHub and merge it into `main`.

## 2. Make keys for phone notifications

```powershell
python -m recallindia vapid-keys
```

Keep the three lines it prints. The private key is a secret: don't commit it.

## 3. Set up Google sign-in

1. Go to **console.cloud.google.com**, create a project named `RecallIndia`, and open **APIs & Services**.
2. **OAuth consent screen:** choose External, set the app name and your email, and save. While it's in "Testing", add your own Gmail under **Test users**.
3. **Credentials > Create credentials > OAuth client ID:** choose type **Web application**.
4. Under **Authorized JavaScript origins**, add `http://localhost:8000` (for your laptop), and after step 4 your Render address, for example `https://recallindia.onrender.com`. No redirect URI is needed.
5. Copy the **Client ID**. It ends in `.apps.googleusercontent.com`.

## 4. Create the service on Render

1. Sign up at **render.com** with your GitHub account.
2. Choose **New > Blueprint**, then pick the `recallindia` repo. Render reads `render.yaml` and plans a web service and a PostgreSQL database.
3. Fill in the values it asks for:
   - `GOOGLE_CLIENT_ID`: from step 3
   - `ADMIN_EMAILS`: your Gmail (it can open the review queue)
   - `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`: from step 2 (`VAPID_SUBJECT` is `mailto:` plus your email)
4. Click **Apply**. The first build takes about 5 minutes. On first start the app creates its tables, loads the FSSAI rules, and loads the bundled snapshot of 236 FoSCoS recalls.
5. Open the `https://….onrender.com` address. Add it to the Google origins (step 3.4), then sign in.

On your phone, open the address in Chrome, sign in, tap **Turn on notifications**, then use the menu's **Add to Home screen**.

## Keeping recalls up to date

The hosted service doesn't run the scraper (it needs a full browser). Run it from your laptop against the hosted database:

1. In Render, open the database and copy its **External Database URL**.
2. On your laptop:
   ```powershell
   $env:DATABASE_URL="<External Database URL>"
   python -m recallindia run --scrape
   ```
   This scrapes FoSCoS, loads new recalls, rematches every user's products, and sends alerts and notifications.

## Things to know about free hosting

- A free web service sleeps when nobody uses it, so the first visit after a while takes up to a minute to wake.
- Free databases have limits and may expire. Check Render's current pricing page and back up anything you care about.
- Alerts are printed to the service log (`ALERT_CHANNEL=console`). To email them, set `ALERT_CHANNEL=email` and the `SMTP_*` values from `.env.example`.
