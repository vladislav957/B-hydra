package io.bhydra.wallet;

import android.app.AlertDialog;
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

import androidx.appcompat.app.AppCompatActivity;

/**
 * Оболочка вокруг кошелька B-hydra.
 *
 * Приложение НЕ содержит криптографии: ключ, подпись и QR — всё это делает
 * сама страница кошелька на устройстве (bhydra-sign.js, bhydra-qr.js). Здесь
 * только окно и адрес узла, у которого страница берётся.
 *
 * Почему так: вторая реализация подписи под Android означала бы третий
 * независимый код для одного формата — и третий источник расхождений. У
 * проекта уже есть правило сверять реализации байт-в-байт, и плодить их без
 * необходимости незачем.
 */
public class MainActivity extends AppCompatActivity {

    private static final String PREFS = "bhydra";
    private static final String NODE_KEY = "node";
    // Узел в локальной сети. 10.0.2.2 — это «хост» из эмулятора Android.
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
        // Без этого приложение забывало бы ключ при каждом закрытии.
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);   // камера для QR

        web.setWebViewClient(new WebViewClient());
        web.loadUrl(nodeUrl() + "/wallet");
    }

    private String nodeUrl() {
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        return prefs.getString(NODE_KEY, DEFAULT_NODE);
    }

    @Override
    public boolean onCreateOptionsMenu(Menu menu) {
        menu.add(0, 1, 0, R.string.menu_node);
        menu.add(0, 2, 0, R.string.menu_reload);
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
                .setTitle(R.string.menu_node)
                .setView(input)
                .setPositiveButton(android.R.string.ok, (dialog, which) -> {
                    String value = input.getText().toString().trim();
                    if (value.isEmpty()) return;
                    if (!value.startsWith("http://") && !value.startsWith("https://")) {
                        value = "http://" + value;
                    }
                    getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                            .putString(NODE_KEY, value).apply();
                    Toast.makeText(this, value, Toast.LENGTH_SHORT).show();
                    web.loadUrl(value + "/wallet");
                })
                .setNegativeButton(android.R.string.cancel, null)
                .show();
    }

    @Override
    public void onBackPressed() {
        // Кнопка «назад» ходит по истории страницы, а не закрывает кошелёк
        // на первом же нажатии.
        if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
