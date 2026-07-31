package io.bhydra.wallet;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.text.InputType;
import android.view.Menu;
import android.view.MenuItem;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.Toast;

/**
 * Оболочка вокруг кошелька B-hydra.
 *
 * Криптографии здесь нет вовсе: ключ, подпись транзакции, QR и выбор узла
 * делает сама страница кошелька на устройстве (bhydra-sign.js, bhydra-qr.js,
 * bhydra-net.js). Java-код — это окно, адрес узла и кнопка «обновить».
 *
 * Вторая реализация подписи под Android означала бы ТРЕТИЙ независимый код
 * для одного формата (после Python и JS) и третий источник расхождений.
 *
 * ⚠️ Ни androidx, ни ресурсов (R.string, @mipmap) здесь намеренно нет:
 * androidx лежит на Google Maven, а ресурсы требуют aapt2 из Android SDK.
 * Без них тот же самый исходник собирается и Gradle-ом, и нашим сборщиком
 * без SDK (b_hydra/apkbuild.py). Строки — прямо в коде.
 */
public class MainActivity extends Activity {

    private static final String PREFS = "bhydra";
    private static final String NODE_KEY = "node";
    // 10.0.2.2 — это «хозяйская» машина, если приложение запущено в ЭМУЛЯТОРЕ.
    // На живом телефоне такого адреса нет, поэтому по умолчанию он НЕ грузится:
    // при первом запуске приложение спрашивает адрес узла (см. onCreate).
    private static final String DEFAULT_NODE = "http://10.0.2.2:8000";

    private WebView web;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        web = new WebView(this);
        setContentView(web);

        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        // Хранилище ОБЯЗАТЕЛЬНО: приватный ключ кошелька живёт в localStorage.
        // Без него приложение забывало бы ключ при каждом закрытии.
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);

        // Свой экран вместо системной страницы ошибки: «ERR_CONNECTION_REFUSED»
        // ничего не подсказывает, а причина почти всегда одна — узел не запущен
        // или у него другой адрес.
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView view, int code, String description,
                                        String failingUrl) {
                showTrouble(failingUrl, description);
            }
        });

        String saved = savedNode();
        if (saved == null) {
            // Первый запуск: адрес узла знает только владелец телефона.
            showWelcome();
            askForNode();
        } else {
            openWallet(saved);
        }
    }

    private String savedNode() {
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        return prefs.getString(NODE_KEY, null);
    }

    private String nodeUrl() {
        String saved = savedNode();
        return saved == null ? DEFAULT_NODE : saved;
    }

    private void openWallet(String node) {
        web.loadUrl(node + "/wallet");
    }

    /** Страница внутри приложения: HTML прямо в коде, ресурсов у нас нет. */
    private void showPage(String title, String body) {
        String html = "<!doctype html><meta charset='utf-8'>"
                + "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                + "<style>body{font:16px/1.5 sans-serif;margin:24px;color:#e8e8f0;"
                + "background:#12121a}h1{font-size:20px;color:#7ee0c8}"
                + "code{background:#22222e;padding:2px 5px;border-radius:4px;"
                + "word-break:break-all}li{margin:8px 0}</style>"
                + "<h1>" + title + "</h1>" + body;
        // Именно loadDataWithBaseURL: loadData ломает UTF-8, и текст станет
        // нечитаемым — а он здесь по-русски.
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
    }

    private void showWelcome() {
        showPage("Кошелёк B-hydra", steps("Осталось указать, где ваш узел."));
    }

    private void showTrouble(String url, String description) {
        showPage("Узел не отвечает",
                "<p>Не удалось открыть <code>" + url + "</code><br>"
                + "<small>" + description + "</small></p>"
                + steps("Приложение — это окно, сама страница кошелька лежит на узле."));
    }

    /** Что делать — одинаково и при первом запуске, и при ошибке связи. */
    private String steps(String intro) {
        return "<p>" + intro + "</p><ol>"
                + "<li>На компьютере запустите узел:<br>"
                + "<code>python -m b_hydra.api --host 0.0.0.0</code></li>"
                + "<li>Телефон и компьютер — в одной сети (Wi-Fi).</li>"
                + "<li>Меню (⋮) → <b>Адрес узла</b> → впишите адрес компьютера,"
                + " например <code>192.168.0.10:8000</code></li></ol>"
                + "<p><small>Адрес <code>10.0.2.2</code> работает только в"
                + " эмуляторе — это его «хозяйская» машина.</small></p>";
    }

    @Override
    public boolean onCreateOptionsMenu(Menu menu) {
        menu.add(0, 1, 0, "Адрес узла");
        menu.add(0, 2, 0, "Обновить");
        return true;
    }

    @Override
    public boolean onOptionsItemSelected(MenuItem item) {
        if (item.getItemId() == 1) {
            askForNode();
            return true;
        }
        if (item.getItemId() == 2) {
            // Именно открыть кошелёк заново, а не reload(): с экрана ошибки
            // (он показан из памяти, без адреса) перезагружать нечего.
            openWallet(nodeUrl());
            return true;
        }
        return super.onOptionsItemSelected(item);
    }

    /** Адрес узла задаёт пользователь: приложение не привязано к одному серверу. */
    private void askForNode() {
        final EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setText(nodeUrl());
        new AlertDialog.Builder(this)
                .setTitle("Адрес узла")
                .setView(input)
                .setPositiveButton(android.R.string.ok, new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        String value = input.getText().toString().trim();
                        if (value.length() == 0) {
                            return;
                        }
                        if (!value.startsWith("http://") && !value.startsWith("https://")) {
                            value = "http://" + value;
                        }
                        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                                .putString(NODE_KEY, value).apply();
                        Toast.makeText(MainActivity.this, value, Toast.LENGTH_SHORT).show();
                        openWallet(value);
                    }
                })
                .setNegativeButton(android.R.string.cancel, null)
                .show();
    }

    @Override
    public void onBackPressed() {
        // «Назад» ходит по истории страницы, а не закрывает кошелёк сразу.
        if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
