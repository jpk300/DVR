package com.jpk300.dvrmanager;

import android.annotation.SuppressLint;
import android.annotation.TargetApi;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.inputmethod.EditorInfo;
import android.webkit.HttpAuthHandler;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * DVR Manager for Fire TV: a full-screen view of your DVR Manager server's web page, in TV mode.
 *
 * <ul>
 *   <li>First launch asks for the server's address (saved for next time).</li>
 *   <li>The page is opened with ?tv=1, which turns on remote-control navigation and TV styling.</li>
 *   <li>Back goes to the page first (it closes dialogs, then returns to Manage); only then does the app close.</li>
 *   <li>Menu (the ☰ button) changes the server address.</li>
 * </ul>
 *
 * The server does all the work (keep rules, history, the daily cleanup), so it must be running on your
 * home network. This app only displays it.
 */
public class MainActivity extends Activity {
    private static final String PREFS = "dvr_manager";
    private static final String KEY_URL = "server_url";
    private static final int BG = Color.rgb(18, 18, 18);

    private WebView web;
    private String serverUrl;
    private boolean setupReturnsToWeb;   // setup opened with Menu: Back goes back to the page
    private boolean failing;             // a load error is already being shown

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        serverUrl = prefs().getString(KEY_URL, null);
        if (serverUrl == null) showSetup(null);
        else showWeb();
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, MODE_PRIVATE);
    }

    /**
     * Turn what was typed into a base URL, or null if it isn't one. "192.168.1.20" becomes
     * "http://192.168.1.20:8080" (the app's default port); a typed scheme or port is kept as given.
     */
    static String normalize(String raw) {
        String s = raw == null ? "" : raw.trim();
        if (s.isEmpty()) return null;
        boolean hadScheme = s.startsWith("http://") || s.startsWith("https://");
        if (!hadScheme) s = "http://" + s;
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        Uri u = Uri.parse(s);
        String host = u.getHost();
        if (host == null || host.isEmpty() || host.contains(" ")) return null;
        if (!hadScheme && u.getPort() == -1) {
            String path = u.getPath() == null ? "" : u.getPath();
            s = "http://" + host + ":8080" + path;
        }
        return s;
    }

    // ---------- the setup screen ----------

    private void showSetup(String error) {
        setupReturnsToWeb = serverUrl != null && error == null;
        failing = false;

        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setBackgroundColor(BG);
        int pad = dp(48);
        box.setPadding(pad, pad, pad, pad);

        TextView title = text("DVR Manager", 34, Color.rgb(82, 150, 66));
        title.setTypeface(Typeface.DEFAULT_BOLD);
        box.addView(title);

        TextView help = text("Enter the address of your DVR Manager server, for example 192.168.1.20:8080. "
                + "It runs on your home network; this app shows its page.", 18, Color.rgb(200, 200, 200));
        help.setPadding(0, dp(12), 0, dp(20));
        box.addView(help);

        final EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setImeOptions(EditorInfo.IME_ACTION_GO);
        input.setHint("192.168.1.20:8080");
        input.setText(serverUrl != null ? serverUrl : "");
        input.setTextSize(TypedValue.COMPLEX_UNIT_SP, 22);
        input.setTextColor(Color.WHITE);
        input.setHintTextColor(Color.GRAY);
        box.addView(input, new LinearLayout.LayoutParams(dp(560), LinearLayout.LayoutParams.WRAP_CONTENT));

        final TextView errorView = text(error == null ? "" : error, 18, Color.rgb(255, 138, 138));
        errorView.setPadding(0, dp(12), 0, 0);
        box.addView(errorView);

        Button connect = new Button(this);
        connect.setText("Connect");
        connect.setTextSize(TypedValue.COMPLEX_UNIT_SP, 20);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(240), LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(20);
        box.addView(connect, lp);

        connect.setOnClickListener(v -> connect(input.getText().toString(), errorView));
        input.setOnEditorActionListener((v, actionId, event) -> {
            connect(input.getText().toString(), errorView);
            return true;
        });

        WebView old = web;
        web = null;
        setContentView(box);
        if (old != null) old.destroy();
        input.requestFocus();
        input.setSelection(input.getText().length());
    }

    private void connect(String typed, TextView errorView) {
        String url = normalize(typed);
        if (url == null) {
            errorView.setText("That doesn't look like an address. Try something like 192.168.1.20:8080");
            return;
        }
        serverUrl = url;
        prefs().edit().putString(KEY_URL, url).apply();
        showWeb();
    }

    // ---------- the page ----------

    @SuppressLint("SetJavaScriptEnabled")
    private void showWeb() {
        failing = false;
        WebView old = web;
        web = new WebView(this);
        web.setBackgroundColor(BG);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setSupportZoom(false);
        web.setWebViewClient(new Client());
        web.setFocusable(true);
        web.setFocusableInTouchMode(true);
        setContentView(web);
        if (old != null) old.destroy();
        web.loadUrl(serverUrl + "/?tv=1");
        web.requestFocus();
    }

    /** A page load failed: go back to setup with the reason (posted, so we're out of the WebView's callback). */
    private void failed(String why) {
        if (failing) return;
        failing = true;
        final String msg = "Couldn't open " + serverUrl + " (" + why + "). Check the server is running, "
                + "or fix the address.";
        getWindow().getDecorView().post(() -> showSetup(msg));
    }

    private class Client extends WebViewClient {
        /** Stay on the DVR Manager server; ignore links anywhere else. */
        @SuppressWarnings("deprecation")
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, String url) {
            Uri u = Uri.parse(url), base = Uri.parse(serverUrl);
            boolean same = u.getHost() != null && u.getHost().equalsIgnoreCase(base.getHost()) && u.getPort() == base.getPort();
            return !same;
        }

        @SuppressWarnings("deprecation")
        @Override
        public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
            if (Build.VERSION.SDK_INT < 23) failed(description);   // newer Android calls the method below
        }

        @TargetApi(23)
        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (request.isForMainFrame()) failed(String.valueOf(error.getDescription()));
        }

        /** The server has APP_USER / APP_PASS set. */
        @Override
        public void onReceivedHttpAuthRequest(WebView view, HttpAuthHandler handler, String host, String realm) {
            askLogin(handler);
        }
    }

    private void askLogin(final HttpAuthHandler handler) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(20);
        box.setPadding(pad, pad, pad, 0);
        final EditText user = new EditText(this);
        user.setHint("User name");
        user.setSingleLine(true);
        final EditText pass = new EditText(this);
        pass.setHint("Password");
        pass.setSingleLine(true);
        pass.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        box.addView(user);
        box.addView(pass);
        new AlertDialog.Builder(this)
                .setTitle("Sign in to DVR Manager")
                .setView(box)
                .setPositiveButton("Sign in", (d, w) -> handler.proceed(user.getText().toString(), pass.getText().toString()))
                .setNegativeButton("Cancel", (d, w) -> handler.cancel())
                .setOnCancelListener(d -> handler.cancel())
                .show();
    }

    // ---------- the remote ----------

    /** Menu (☰) changes the server. Handled here, before the page sees the key. */
    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        if (event.getKeyCode() == KeyEvent.KEYCODE_MENU) {
            if (event.getAction() == KeyEvent.ACTION_UP) showSetup(null);
            return true;
        }
        return super.dispatchKeyEvent(event);
    }

    /** Back: let the page handle it first (close a dialog, return to Manage); close the app if it doesn't. */
    @SuppressWarnings("deprecation")
    @Override
    public void onBackPressed() {
        if (web == null) {
            if (setupReturnsToWeb) showWeb();
            else super.onBackPressed();
            return;
        }
        web.evaluateJavascript("(window.tvBack && window.tvBack()) ? 'yes' : 'no'", value -> {
            if (!"\"yes\"".equals(value)) finish();
        });
    }

    // ---------- lifecycle ----------

    @Override
    protected void onPause() {
        super.onPause();
        if (web != null) web.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null) web.onResume();
    }

    @Override
    protected void onDestroy() {
        if (web != null) web.destroy();
        web = null;
        super.onDestroy();
    }

    // ---------- helpers ----------

    private int dp(float v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics());
    }

    private TextView text(String s, float sp, int color) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        t.setGravity(Gravity.CENTER);
        return t;
    }
}
