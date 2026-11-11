from __future__ import annotations

import os
import stat
from pathlib import Path

from ..core.models import Context, Status
from .base import Collector


def temp_roots() -> list[dict]:
    """Os tres diretorios exigidos. Caminhos duplicados sao sinalizados, nunca contados duas vezes."""
    cands = [
        ("TEMP", "%TEMP%", os.environ.get("TEMP")),
        ("LOCALAPPDATA_TEMP", r"%LOCALAPPDATA%\Temp",
         os.path.join(os.environ["LOCALAPPDATA"], "Temp") if os.environ.get("LOCALAPPDATA") else None),
        ("WINDOWS_TEMP", r"C:\Windows\Temp",
         os.path.join(os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows", "Temp")),
    ]
    seen, out = {}, []
    for rid, label, path in cands:
        if not path:
            continue
        norm = os.path.normcase(os.path.realpath(path))
        out.append({"id": rid, "rotulo": label, "caminho": path, "duplicado_de": seen.get(norm)})
        seen.setdefault(norm, rid)
    return out


def _is_reparse(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except OSError:
        return True


def is_locked(path: str) -> bool:
    try:
        with open(path, "rb"):
            return False
    except OSError:
        return True


def scan_dir(root: str) -> dict:
    files = folders = size = locked = locked_size = 0
    errors = []
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    if _is_reparse(e):
                        continue  # nunca segue links/junctions
                    try:
                        if e.is_dir(follow_symlinks=False):
                            folders += 1
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            files += 1
                            sz = e.stat(follow_symlinks=False).st_size
                            size += sz
                            if is_locked(e.path):
                                locked += 1
                                locked_size += sz
                    except OSError as ex:
                        errors.append(f"{e.path}: {ex}")
        except OSError as ex:
            errors.append(f"{cur}: {ex}")
    return {"arquivos": files, "pastas": folders, "bytes": size, "bloqueados": locked,
            "bytes_bloqueados": locked_size, "erros": errors[:20]}


def clean_dir(root: str, allowed_roots: list[str]) -> dict:
    """Remove arquivos/pastas elegiveis dentro de um diretorio temporario permitido.
    Nunca forca exclusao de arquivos em uso: falhas sao apenas registradas."""
    real = os.path.normcase(os.path.realpath(root))
    allowed = {os.path.normcase(os.path.realpath(a)) for a in allowed_roots}
    if real not in allowed or Path(real).parent == Path(real):
        raise PermissionError(f"Diretório não autorizado para limpeza: {root}")
    freed = removed = 0
    failed: list[dict] = []
    for cur, dirs, files in os.walk(root, topdown=False, followlinks=False):
        for name in files:
            p = os.path.join(cur, name)
            try:
                if os.path.islink(p):
                    os.unlink(p)
                    continue
                size = os.path.getsize(p)
                try:
                    os.remove(p)
                except PermissionError:
                    os.chmod(p, stat.S_IWRITE)   # apenas remove atributo somente-leitura
                    os.remove(p)
                freed += size
                removed += 1
            except OSError as ex:
                failed.append({"caminho": p, "motivo": "Em uso ou sem permissão" if isinstance(ex, PermissionError) else str(ex)})
        for d in dirs:
            p = os.path.join(cur, d)
            try:
                if os.path.islink(p) or (getattr(os.stat(p, follow_symlinks=False), "st_file_attributes", 0) & 0x400):
                    continue
                os.rmdir(p)
            except OSError:
                pass  # pasta nao vazia (arquivos bloqueados) ou em uso
    return {"liberado_bytes": freed, "removidos": removed, "falhas": failed}


class TempFilesCollector(Collector):
    key = "temp_files"
    title = "Arquivos temporários"

    def run(self, ctx: Context):
        dirs, total_rec = [], 0
        for r in temp_roots():
            item = {"id": r["id"], "caminho": r["caminho"], "arquivos": None, "pastas": None,
                    "tamanho_mb": None, "recuperavel_mb": None, "bloqueados": None, "observacao": None}
            if r["duplicado_de"]:
                item["observacao"] = f"Mesmo caminho de {r['duplicado_de']} (não contado novamente)"
            elif not os.path.isdir(r["caminho"]):
                item["observacao"] = "Diretório inexistente"
            else:
                s = scan_dir(r["caminho"])
                item.update({"arquivos": s["arquivos"], "pastas": s["pastas"],
                             "tamanho_mb": round(s["bytes"] / 1024 ** 2, 1),
                             "recuperavel_mb": round((s["bytes"] - s["bytes_bloqueados"]) / 1024 ** 2, 1),
                             "bloqueados": s["bloqueados"]})
                total_rec += s["bytes"] - s["bytes_bloqueados"]
                if s["erros"]:
                    item["observacao"] = f"{len(s['erros'])} item(ns) inacessível(is)" + (
                        " (execute como administrador)" if not ctx.is_admin else "")
            dirs.append(item)
        data = {"diretorios": dirs, "total_recuperavel_mb": round(total_rec / 1024 ** 2, 1)}
        problems = []
        if total_rec > 2 * 1024 ** 3:
            problems.append(f"Arquivos temporários ocupam {data['total_recuperavel_mb']} MB recuperáveis")
        admin = [] if ctx.is_admin else ["Leitura completa de C:\\Windows\\Temp"]
        return self.new(Status.ALERT if problems else Status.OK,
                        f"{data['total_recuperavel_mb']} MB potencialmente recuperáveis", data=data,
                        problems=problems, admin_required=admin)
