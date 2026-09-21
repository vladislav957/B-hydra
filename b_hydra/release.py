"""Подпись релиза — инструмент ВЛАДЕЛЬЦА проекта, не пользователя.

    python -m b_hydra.release keygen --out ~/.bhydra-release.key
    python -m b_hydra.release sign --key ~/.bhydra-release.key \\
        --version v0.1.1 B-hydra-Core-windows.exe B-hydra-Core-macos \\
        B-hydra-Core-linux
    python -m b_hydra.release verify --manifest bhydra-release.json \\
        --signature bhydra-release.json.sig --key-file release.pub

`keygen` делается ОДИН РАЗ за всю жизнь проекта, `sign` — на каждый релиз.
Получившиеся `bhydra-release.json` и `bhydra-release.json.sig` прикладываются
к релизу на GitHub рядом со сборками.

⚠️ ЗАКРЫТЫЙ КЛЮЧ В РЕПОЗИТОРИЙ НЕ КЛАДЁТСЯ НИКОГДА и в GitHub Actions тоже.
Ключ в CI защищает ровно от того, от чего уже защищает TLS, и не защищает от
угона учётной записи — то есть от единственной угрозы, ради которой подпись и
вводится. Смысл появляется только тогда, когда ключ лежит там, куда взлом
сервера не достаёт. Файл пишется с правами 0600, но это лишь от случайного
чтения соседом по машине, а не замена офлайн-хранению.

⚠️ Хеши файлов считаются `hashlib` — см. шапку `updater.py`: наш чистый SHA-512
проверял бы сборку Linux 38 секунд против 0,04 с при тех же байтах.
"""

import argparse
import hashlib
import json
import os
import sys

from . import rsa
from .updater import MANIFEST_NAME, SIGNATURE_NAME, manifest_payload
from .version import format_version

DEFAULT_BITS = 3072


def _read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def cmd_keygen(args):
    if os.path.exists(args.out) and not args.force:
        print(f"Файл {args.out} уже существует. Перезаписать — --force.\n"
              f"⚠️ Потеря ключа означает, что подписывать новые релизы будет "
              f"нечем, а замена ключа потребует от всех переустановки вручную.",
              file=sys.stderr)
        return 1

    print(f"Генерация ключа на {args.bits} бит (это небыстро)…", file=sys.stderr)
    key = rsa.generate(args.bits)

    # 0600 ставится ДО записи: создать файл всем на чтение и сузить права
    # потом — значит оставить окно, в котором ключ читается кем угодно.
    handle = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(rsa.private_to_pem(key, pkcs8=True))

    public = rsa.public_to_pem(key.public(), spki=True)
    if args.public:
        with open(args.public, "w", encoding="utf-8") as stream:
            stream.write(public)

    print(f"Закрытый ключ: {args.out} (права 0600)", file=sys.stderr)
    print("⚠️ Унесите его в офлайн. В репозиторий и в CI — НИКОГДА.\n",
          file=sys.stderr)
    print("Открытый ключ — вставьте в b_hydra/release_key.py как\n"
          "RELEASE_PUBLIC_KEY = \"\"\"\\", file=sys.stderr)
    print(public.rstrip())
    print('"""', file=sys.stderr)
    return 0


def build_manifest(version: str, paths) -> dict:
    files = {}
    for path in paths:
        name = os.path.basename(path)
        if name in files:
            raise SystemExit(f"файл {name} указан дважды")
        files[name] = hashlib.sha512(_read(path)).hexdigest()
    if not files:
        raise SystemExit("нечего подписывать: не указано ни одного файла")
    return {"version": format_version(version), "files": files}


def cmd_sign(args):
    for path in args.files:
        if not os.path.isfile(path):
            print(f"нет файла: {path}", file=sys.stderr)
            return 1

    key = rsa.private_from_pem(_read(args.key).decode("utf-8"))
    manifest = build_manifest(args.version, args.files)
    raw = manifest_payload(manifest)
    signature = rsa.sign_pss(key, raw)

    # ⚠️ Подпись проверяется СРАЗУ ЖЕ своим же открытым ключом. Выпустить
    # релиз с непроверяемой подписью — значит узнать об этом от пользователей,
    # у которых обновление откажется ставиться.
    if not rsa.verify_pss(key.public(), raw, signature):
        print("собственная подпись не проверяется — релиз НЕ подписан",
              file=sys.stderr)
        return 1

    with open(args.manifest, "wb") as stream:
        stream.write(raw)
    with open(args.signature, "wb") as stream:
        stream.write(signature)

    print(f"Манифест : {args.manifest}", file=sys.stderr)
    print(f"Подпись  : {args.signature} ({len(signature)} байт)", file=sys.stderr)
    print(f"Версия   : {manifest['version']}", file=sys.stderr)
    for name, digest in sorted(manifest["files"].items()):
        print(f"  {name:32} {digest[:32]}…", file=sys.stderr)
    print("\nПриложите оба файла к релизу на GitHub рядом со сборками.",
          file=sys.stderr)
    return 0


def cmd_verify(args):
    from . import release_key
    from .updater import UpdateError, verify_manifest

    pem = None
    if args.key_file:
        pem = _read(args.key_file).decode("utf-8")
    elif release_key.have_key():
        pem = release_key.RELEASE_PUBLIC_KEY
    else:
        print("ключ не задан: ни --key-file, ни RELEASE_PUBLIC_KEY",
              file=sys.stderr)
        return 1

    try:
        manifest = verify_manifest(_read(args.manifest), _read(args.signature), pem)
    except UpdateError as error:
        print(f"НЕ ПРОВЕРЕНО: {error}", file=sys.stderr)
        return 1
    print(f"Подпись верна. Версия {manifest['version']}, "
          f"файлов {len(manifest['files'])}.", file=sys.stderr)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="b-hydra-release",
        description="Подпись релизов B-hydra Core (инструмент владельца)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_keygen = sub.add_parser("keygen", help="создать ключ релизов (один раз)")
    p_keygen.add_argument("--out", default="bhydra-release.key",
                          help="куда положить ЗАКРЫТЫЙ ключ")
    p_keygen.add_argument("--public", help="куда положить открытый ключ")
    p_keygen.add_argument("--bits", type=int, default=DEFAULT_BITS)
    p_keygen.add_argument("--force", action="store_true")
    p_keygen.set_defaults(func=cmd_keygen)

    p_sign = sub.add_parser("sign", help="подписать сборки релиза")
    p_sign.add_argument("files", nargs="+", help="файлы сборок")
    p_sign.add_argument("--key", required=True, help="закрытый ключ релизов")
    p_sign.add_argument("--version", required=True, help="версия релиза")
    p_sign.add_argument("--manifest", default=MANIFEST_NAME)
    p_sign.add_argument("--signature", default=SIGNATURE_NAME)
    p_sign.set_defaults(func=cmd_sign)

    p_verify = sub.add_parser("verify", help="проверить подпись манифеста")
    p_verify.add_argument("--manifest", default=MANIFEST_NAME)
    p_verify.add_argument("--signature", default=SIGNATURE_NAME)
    p_verify.add_argument("--key-file", help="открытый ключ (по умолчанию — вшитый)")
    p_verify.set_defaults(func=cmd_verify)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
