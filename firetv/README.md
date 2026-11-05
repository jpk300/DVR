# DVR Manager for Fire TV

A small Fire TV app that shows your DVR Manager server's page full-screen, made to work with the Fire TV remote.

The app is only a screen and a remote. Your **DVR Manager server still does all the work** (keep rules, protection, history, the daily cleanup), so it must be running on your home network. The server's page has a **TV mode**, which the app switches on. Browsers are unaffected.

## 1. Set up the server

Follow the [Quick start](../README.md#quick-start) in the main README. A fresh clone is on `firetv`, the default branch, which has TV mode.

If your server was cloned before `firetv` became the default, it may still be on `main`, which doesn't have TV mode. Check with `git branch --show-current`, and if it says `main`, switch once, in the folder where you cloned the repo:

```bash
git fetch
git switch firetv
docker compose up -d --build
```

## 2. Install the app on the Fire TV

1. **Allow sideloading.** On the Fire TV: **Settings → My Fire TV → Developer options → Install unknown apps**, and turn it on for **Downloader**. On older models it's **Apps from Unknown Sources**. If you don't see **Developer options**, go to **Settings → My Fire TV → About** and press OK on the device name seven times.
2. **Install Downloader** (by AFTVnews) from the Amazon Appstore, and open it.
3. **Enter this address** and press Go:

   ```
   https://github.com/jpk300/DVR/releases/download/firetv-latest/dvr-manager-firetv.apk
   ```

   Downloader fetches the app; choose **Install**, then **Done**. You can let it delete the downloaded file afterwards.
4. **Open DVR Manager** from **Your Apps & Channels**. To pin it to the home row, highlight it, press ☰ (Menu) and choose **Move to front**.
5. **Enter your server's address** the first time, for example `192.168.1.20:8080`. If you leave out the port, `:8080` is assumed.

If the app can't reach the server it says so, and goes back to the address screen.

<details>
<summary>Installing with adb instead</summary>

Enable **ADB debugging** in **Developer options**, find the Fire TV's IP address under **Settings → My Fire TV → About → Network**, then on a computer:

```bash
adb connect <fire-tv-ip>:5555
adb install -r dvr-manager-firetv.apk
```
</details>

## Using the remote

| Button | What it does |
|---|---|
| **◀ ▲ ▼ ▶** | Move between shows, buttons and recordings. The highlighted item has a yellow outline |
| **OK** (centre) | Press the highlighted button, or tick the highlighted checkbox |
| **Back** | Close the open dialog. On History or Settings, return to Manage. On Manage, leave the app |
| **☰ Menu** | Change the server address |

Everything on the page works from the remote. Typing (search, the DVR address, the timezone) opens the Fire TV keyboard. Numbers are never typed: each one has **−** and **+** beside it.

**Settings:** a bar at the top lists its four sections (DVR connection, Automatic cleanup, New shows, Free space warning). Press OK on one to jump to it, or press ▼ to go through them in order.

**Moving around:**
- The top bar (**Manage**, **History**, **Settings**) stays at the top of the screen while you scroll. The automatic-cleanup status (on or off, next run, last run) is in a bar along the bottom.
- ▲ and ▼ stay inside the area you're in: a show's recordings panel on the left, the show list on the right, or the top bar. They only move on when nothing is left in that direction, and then only to what's directly above or below, such as the top bar.
- ◀ and ▶ move along the same line: between a recording's checkbox and its lock, or across from the panel to the show list.
- The recordings panel scrolls on its own to reach older recordings. Moving back up to its top, or across to the show list, scrolls it back to the top, so the show's picture is in view again.

**Changing a keep rule:** highlight a show's **Keep** button and press OK. In the dialog, move to an option and press OK to choose it, then press ▶ to reach **−** and **+** and set the number. The line under the options says what the rule would delete. Move down to **Save rule** and press OK, or press Back to cancel.

## Updating the app

Most changes are to the server's page, and reach the TV when you [update the server](#updating-the-server); the app doesn't change. Only a change under `firetv/` builds a new APK, which replaces the download at the address above. To update the app, reinstall it with Downloader the same way.

By default the APK is a **debug build**, signed with a key that changes every build. Android then refuses to install it over the old one, so **uninstall the old version first**: **Settings → Applications → Manage Installed Applications → DVR Manager → Uninstall**. The server address is forgotten, so enter it again.

To update in place instead, give CI your own signing key once. You need Docker, and the `gh` CLI signed in to this repository:

```bash
docker run --rm -v "$PWD":/w -w /w eclipse-temurin:17 keytool -genkeypair -keystore dvr-firetv.jks \
  -alias dvr -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=DVR Manager"
base64 -i dvr-firetv.jks | gh secret set FIRETV_KEYSTORE_BASE64
gh secret set FIRETV_KEYSTORE_PASSWORD   # the store password you chose
gh secret set FIRETV_KEY_PASSWORD        # the key password (the same one, unless you set it separately)
gh secret set FIRETV_KEY_ALIAS --body dvr
```

Keep `dvr-firetv.jks` somewhere safe, outside the repo; git ignores `*.jks` here. From the next build on, CI signs with it. Uninstall the debug version once, install the signed one, and later builds install over it.

## Updating the server

To bring your server up to date, in the folder where you cloned the repo:

```bash
git pull
docker compose up -d --build
```

Then reopen the app on the Fire TV. It only needs reinstalling when something under `firetv/` changes.

## How it fits together

- `static/js/tv.js` is TV mode in the web page. It moves focus with the arrow keys, keeping to areas of the page (the `AREAS` list), keeps focus in place when the page redraws, makes OK tick checkboxes, and provides `window.tvBack()` for the Back button. It also makes number boxes read-only, so − / + set them and the on-screen keyboard never opens for them. Larger type, the focus ring, the fixed top and bottom bars, the Settings section bar and the − / + buttons are styled in `static/app.css`, under "TV mode". `static/js/ruledialog.js` and `static/js/settings.js` handle − / + in the keep-rule dialog and on Settings.
- `firetv/android/` is the Android app: one Activity (`MainActivity.java`), with no libraries. It opens `http://<server>/?tv=1` in a full-screen WebView, sends Back to the page first, uses Menu to change the server, and asks for a user name and password if the server uses `APP_USER` / `APP_PASS`.
- `.github/workflows/firetv.yml` builds the APK with Gradle on GitHub Actions whenever `firetv/` changes on the `firetv` branch, and publishes it as the `firetv-latest` pre-release. There's no Gradle wrapper; CI installs Gradle 8.10.2. To build locally you need JDK 17, the Android SDK and Gradle: `cd firetv/android && gradle assembleDebug`.
