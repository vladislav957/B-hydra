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

## Автоматическая сборка под все ОС (GitHub Actions)

Workflow `.github/workflows/build.yml` собирает бинарники под Windows, macOS и
Linux и публикует их как артефакты. Запуск:

- вручную: вкладка **Actions → build → Run workflow**;
- автоматически: при пуше тега вида `v1.0.0`.

Готовые файлы (`bhydra-windows`, `bhydra-macos`, `bhydra-linux`) скачиваются со
страницы запуска workflow.

[PyInstaller]: https://pyinstaller.org/
