package com.wushuang.remote;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.os.Bundle;
import android.view.Gravity;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

/**
 * 无双遥控器：WebView 封装 NAS 后台「播放」页。
 * 首次启动配置服务器地址；登录态由 WebView Cookie 持久保存。
 * 零第三方依赖，仅 Android framework。
 */
public class MainActivity extends Activity {
    private static final String KEY = "server_url";
    private WebView web;
    private FrameLayout root;
    private SharedPreferences prefs;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences("remote", MODE_PRIVATE);
        root = new FrameLayout(this);
        setContentView(root);
        String url = prefs.getString(KEY, "");
        if (url.isEmpty()) {
            showSetup();
        } else {
            openWeb(url);
        }
    }

    private String normalize(String u) {
        u = u.trim();
        if (!u.startsWith("http://") && !u.startsWith("https://")) u = "http://" + u;
        while (u.endsWith("/")) u = u.substring(0, u.length() - 1);
        return u;
    }

    private void showSetup() {
        root.removeAllViews();
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(60, 140, 60, 60);
        TextView title = new TextView(this);
        title.setText("🎵 无双遥控器");
        title.setTextSize(26);
        TextView hint = new TextView(this);
        hint.setText("输入 NAS 后台地址（手机与 NAS 需同局域网）\n"
                + "例如 http://192.168.1.10:8080\n\n"
                + "连接后登录一次，点顶部「播放」即可点歌/控制/发原生指令。");
        hint.setPadding(0, 24, 0, 24);
        hint.setLineSpacing(8, 1);
        final EditText input = new EditText(this);
        input.setHint("http://NAS_IP:8080");
        input.setSingleLine(true);
        input.setText(prefs.getString(KEY, ""));
        Button go = new Button(this);
        go.setText("连接");
        go.setOnClickListener(v -> {
            String u = input.getText().toString().trim();
            if (u.isEmpty()) {
                Toast.makeText(this, "请输入地址", Toast.LENGTH_SHORT).show();
                return;
            }
            u = normalize(u);
            prefs.edit().putString(KEY, u).apply();
            openWeb(u);
        });
        box.addView(title);
        box.addView(hint);
        box.addView(input);
        box.addView(go);
        root.addView(box, new FrameLayout.LayoutParams(-1, -1));
    }

    private void openWeb(String base) {
        root.removeAllViews();
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setSupportZoom(false);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView v, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) {
                    Toast.makeText(MainActivity.this,
                            "连接失败：检查地址与局域网（点右上角⚙修改）", Toast.LENGTH_LONG).show();
                }
            }
        });
        web.setWebChromeClient(new WebChromeClient());
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));

        Button gear = new Button(this);
        gear.setText("⚙");
        gear.setTextSize(16);
        gear.setBackgroundColor(Color.argb(150, 28, 36, 49));
        gear.setTextColor(Color.WHITE);
        FrameLayout.LayoutParams lp =
                new FrameLayout.LayoutParams(96, 96, Gravity.TOP | Gravity.END);
        lp.topMargin = 12;
        lp.rightMargin = 12;
        gear.setOnClickListener(v -> showServerDialog());
        root.addView(gear, lp);

        web.loadUrl(base + "/player");
    }

    private void showServerDialog() {
        final EditText input = new EditText(this);
        input.setText(prefs.getString(KEY, ""));
        input.setSingleLine(true);
        new AlertDialog.Builder(this)
                .setTitle("服务器地址")
                .setView(input)
                .setPositiveButton("连接", (d, w) -> {
                    String u = normalize(input.getText().toString());
                    prefs.edit().putString(KEY, u).apply();
                    openWeb(u);
                })
                .setNegativeButton("取消", null)
                .show();
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
