# Сборка B-hydra в исполняемый файл (.exe / .app / бинарь)

Десктоп-клиент `bhydra_gui.py` можно упаковать в **один файл** с помощью
[PyInstaller] — пользователю не нужен установленный Python.

> ⚠️ Важно: PyInstaller собирает под **ту ОС, на которой запущен**.
> Windows `.exe` собирается **на Windows**, macOS `.app` — на macOS, и т.д.
> Кросс-компиляции нет. Готовые бинарники под все ОС собирает CI (см. ниже).

## Быстрая сборка (у себя)

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name bhydra bhydra_gui.py
```

Результат появится в папке `dist/`:
- Windows → `dist\bhydra.exe`
- macOS   → `dist/bhydra.app` (и `dist/bhydra`)
- Linux   → `dist/bhydra`

Флаги:
- `--onefile`   — всё в одном файле;
- `--windowed`  — без чёрного окна консоли (для GUI);
- `--name`      — имя результата.

### Linux: нужен tkinter
```bash
sudo apt-get install -y python3-tk
```

### С иконкой

Иконка лежит в `assets/` (генерируется `python tools/make_icon.py`). Чтобы
вшить её в `.exe` и в окно:

```bash
# Windows:
pyinstaller --onefile --windowed --name bhydra --icon assets/bhydra.ico \
  --add-data "assets/bhydra.png;assets" bhydra_gui.py
# macOS/Linux (разделитель в --add-data — двоеточие):
pyinstaller --onefile --windowed --name bhydra \
  --add-data "assets/bhydra.png:assets" bhydra_gui.py
```

## Автоматическая сборка под все ОС (GitHub Actions)

Workflow `.github/workflows/build.yml` собирает бинарники под Windows, macOS и
Linux (с иконкой) и:

- публикует их как **артефакты** на странице запуска;
- при пуше тега `v1.0.0` — выкладывает в **GitHub Releases** для скачивания.

В релиз попадают три файла с суффиксом платформы:

```
B-hydra-Core-windows.exe
B-hydra-Core-macos
B-hydra-Core-linux
```

⚠️ Суффикс обязателен. Раньше macOS и Linux выкладывали одинаковый
`B-hydra-Core`, и в релизе оставался только тот, кто успел последним: у v0.0.6
и v0.0.7 приехало по два бинарника вместо трёх, причём молча — сборка при этом
считается успешной. Само приложение по-прежнему называется `B-hydra-Core`,
суффикс есть только у файла в релизе.

Запуск:
- вручную: вкладка **Actions → build → Run workflow**
  (можно выбрать не только ветку, но и **тег** — тогда бинарники приложатся
  к релизу этого тега);
- релиз: `git tag v1.0.0 && git push --tags`.

⚠️ **Черновик релиза.** Если релиз сохранён черновиком и опубликован позже,
события `push` для тега НЕ будет: пока релиз черновик, тега ещё нет, а
публикация создаёт его молча. Именно поэтому у `v.0.0.8` не оказалось `.exe` —
сборка не запускалась вовсе. Теперь workflow слушает и `release: published`,
так что этот путь закрыт.
⚠️ Событие `release` берёт workflow из **ветки по умолчанию**, а `push` тега —
из коммита, на который указывает тег. Правку в `build.yml` нужно влить в `main`,
иначе для черновиков она не применится.

[PyInstaller]: https://pyinstaller.org/
