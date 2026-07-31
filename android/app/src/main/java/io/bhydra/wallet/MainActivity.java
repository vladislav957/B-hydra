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
    // 10.0.2.2 — это «хозяйская» машина, если приложение запущено в эмуляторе.
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

        web.setWebViewClient(new WebViewClient());
        web.loadUrl(nodeUrl() + "/wallet");
    }

    private String nodeUrl() {
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        return prefs.getString(NODE_KEY, DEFAULT_NODE);
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
            web.reload();
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
                        web.loadUrl(value + "/wallet");
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
