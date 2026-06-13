from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .material_db import MaterialDatabase
from .material_db.database import now_iso


PUBLIC_SITE_ROOT = Path(__file__).with_name("web") / "public_materials"
PUBLIC_SITE_FILES = ("index.html", "styles.css", "app.js")
PUBLIC_SITE_ASSET_DIRS = ("assets",)
DATA_FILE_NAME = "material-data.json"
PUBLISH_FILES = (*PUBLIC_SITE_FILES, DATA_FILE_NAME, *PUBLIC_SITE_ASSET_DIRS)
DEFAULT_OFFICIAL_SITE_CONTENT_VERSION = "2026-06-01-nte-official-home"

DEFAULT_OFFICIAL_SITE_CONTENT: tuple[dict[str, Any], ...] = (
    {
        "section": "meta",
        "item_key": "main",
        "title": "石器时代-精灵召唤",
        "subtitle": "官方网站",
        "body": "回到尼斯大陆，召唤精灵、组建家族、挑战副本，把官方公告、攻略与资料入口集中在这里。",
        "url": "https://www.djinhe.cn/",
        "badge": "官方论坛",
        "meta": {"author": "烈焰部落 - 花儿", "service_wechat": "djinhe"},
    },
    {
        "section": "stats",
        "item_key": "download",
        "sort_order": 10,
        "title": "游戏下载",
        "body": "客户端下载与更新入口",
        "url": "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=2",
        "badge": "入口",
    },
    {
        "section": "stats",
        "item_key": "announcements",
        "sort_order": 20,
        "title": "游戏公告",
        "body": "版本更新与维护说明",
        "url": "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=3",
        "badge": "公告",
    },
    {
        "section": "stats",
        "item_key": "support",
        "sort_order": 30,
        "title": "客服微信",
        "body": "djinhe",
        "url": "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=7",
        "badge": "客服",
    },
    {
        "section": "boards",
        "item_key": "download",
        "sort_order": 10,
        "title": "游戏下载",
        "body": "客户端下载、安装说明与更新入口。",
        "url": "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=2",
        "badge": "下载",
        "meta": {
            "posts": [
                {"title": "游戏下载", "url": "https://www.djinhe.cn/forum.php?mod=viewthread&tid=1", "author": "admin"},
                {"title": "下載", "url": "https://www.djinhe.cn/forum.php?mod=viewthread&tid=16", "author": "admin"},
            ]
        },
    },
    {
        "section": "boards",
        "item_key": "announcements",
        "sort_order": 20,
        "title": "游戏公告",
        "body": "版本更新、活动上下架、奖励调整与维护说明。",
        "url": "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=3",
        "badge": "公告",
        "meta": {
            "posts": [
                {"title": "4月4日21点不停机更新，如有争议将重新调整", "url": "https://www.djinhe.cn/forum.php?mod=viewthread&tid=15", "author": "admin"},
                {"title": "3月27日23点不停机更新，如有争议将重新调整", "url": "https://www.djinhe.cn/forum.php?mod=viewthread&tid=14", "author": "admin"},
                {"title": "3月23日14点30不停机更新", "url": "https://www.djinhe.cn/forum.php?mod=viewthread&tid=13", "author": "admin"},
                {"title": "3月21日15点不停机更新", "url": "https://www.djinhe.cn/forum.php?mod=viewthread&tid=12", "author": "admin"},
                {"title": "3月14日18点30不停机更新", "url": "https://www.djinhe.cn/forum.php?mod=viewthread&tid=8", "author": "admin"},
                {"title": "2月28日中午11点不停机更新", "url": "https://www.djinhe.cn/forum.php?mod=viewthread&tid=7", "author": "admin"},
                {"title": "2月14日18點不停機更新", "url": "https://www.djinhe.cn/forum.php?mod=viewthread&tid=2", "author": "admin"},
            ]
        },
    },
    {
        "section": "boards",
        "item_key": "support-qa",
        "sort_order": 30,
        "title": "问题解答",
        "body": "客服与常见问题入口，后续可继续补充官方 FAQ。",
        "url": "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=4",
        "badge": "答疑",
        "meta": {
            "posts": [
                {"title": "客服微信：djinhe", "url": "https://www.djinhe.cn/forum.php?mod=viewthread&tid=3", "author": "admin"},
            ]
        },
    },
    {
        "section": "boards",
        "item_key": "guide",
        "sort_order": 40,
        "title": "攻略分享",
        "body": "过滤玩家广告贴，只展示 admin 维护的攻略内容。",
        "url": "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=5",
        "badge": "攻略",
        "meta": {
            "posts": [
                {"title": "游戏设置", "url": "https://www.djinhe.cn/forum.php?mod=viewthread&tid=9", "author": "admin"},
            ]
        },
    },
    {
        "section": "boards",
        "item_key": "training",
        "sort_order": 50,
        "title": "练宠活动",
        "body": "练宠活动与奖励说明。",
        "url": "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=6",
        "badge": "活动",
        "meta": {
            "posts": [
                {"title": "赤炼灵姬练宠活动", "url": "https://www.djinhe.cn/forum.php?mod=viewthread&tid=4", "author": "admin"},
            ]
        },
    },
    {
        "section": "boards",
        "item_key": "support-wechat",
        "sort_order": 60,
        "title": "客服微信",
        "body": "官方客服微信入口。",
        "url": "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=7",
        "badge": "客服",
        "meta": {
            "posts": [
                {"title": "客服微信", "url": "https://www.djinhe.cn/forum.php?mod=viewthread&tid=6", "author": "admin"},
            ]
        },
    },
    {
        "section": "boards",
        "item_key": "family",
        "sort_order": 70,
        "title": "家族收人",
        "body": "家族招募与组队社群入口。",
        "url": "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=8",
        "badge": "家族",
        "meta": {"posts": []},
    },
    {
        "section": "boards",
        "item_key": "lucky-card",
        "sort_order": 80,
        "title": "来吉卡",
        "body": "来吉卡相关说明与活动入口。",
        "url": "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=9",
        "badge": "来吉卡",
        "meta": {
            "posts": [
                {"title": "来吉卡", "url": "https://www.djinhe.cn/forum.php?mod=viewthread&tid=5", "author": "admin"},
            ]
        },
    },
    {
        "section": "announcements",
        "item_key": "update-20260404",
        "sort_order": 10,
        "title": "4月4日21点不停机更新",
        "subtitle": "游戏公告",
        "body": "最新更新公告以官方论坛原帖为准。",
        "url": "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=3",
        "badge": "更新",
    },
    {
        "section": "announcements",
        "item_key": "download",
        "sort_order": 20,
        "title": "游戏下载",
        "subtitle": "游戏下载",
        "body": "客户端下载、补丁与安装说明集中在论坛下载版块。",
        "url": "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=2",
        "badge": "下载",
    },
    {
        "section": "announcements",
        "item_key": "training-event",
        "sort_order": 30,
        "title": "赤炼灵姬练宠活动",
        "subtitle": "练宠活动",
        "body": "活动规则与奖励以论坛活动帖为准。",
        "url": "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=6",
        "badge": "活动",
    },
    {
        "section": "features",
        "item_key": "summon",
        "sort_order": 10,
        "title": "精灵召唤",
        "subtitle": "收集与养成",
        "body": "围绕精灵培养、练宠活动和长期成长路线做内容整理。",
        "badge": "召唤",
    },
    {
        "section": "features",
        "item_key": "craft",
        "sort_order": 20,
        "title": "装备打造",
        "subtitle": "材料与升级",
        "body": "资料库作为独立工具入口提供价格、出处、配方和升级路线计算。",
        "badge": "打造",
    },
    {
        "section": "features",
        "item_key": "tribe",
        "sort_order": 30,
        "title": "家族协作",
        "subtitle": "组队与社群",
        "body": "家族收人、组队副本和攻略分享都可从官网入口进入。",
        "badge": "家族",
    },
    {
        "section": "features",
        "item_key": "economy",
        "sort_order": 40,
        "title": "市场交易",
        "subtitle": "钻石与税率",
        "body": "交易税率计算器保留在资料工具内，方便玩家快速换算到账与扣税。",
        "badge": "交易",
    },
    {
        "section": "links",
        "item_key": "forum",
        "sort_order": 10,
        "title": "官方论坛",
        "body": "公告、攻略、活动与客服入口",
        "url": "https://www.djinhe.cn/",
        "badge": "论坛",
    },
    {
        "section": "links",
        "item_key": "guide",
        "sort_order": 20,
        "title": "攻略分享",
        "body": "玩家经验与副本资料",
        "url": "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=5",
        "badge": "攻略",
    },
    {
        "section": "links",
        "item_key": "family",
        "sort_order": 30,
        "title": "家族收人",
        "body": "家族招募与组队信息",
        "url": "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=8",
        "badge": "家族",
    },
    {
        "section": "links",
        "item_key": "lucky-card",
        "sort_order": 40,
        "title": "来吉卡",
        "body": "相关活动与说明",
        "url": "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=9",
        "badge": "活动",
    },
)


@dataclass
class MaterialSiteSyncResult:
    output_dir: Path
    git_repo: bool
    changed: bool
    committed: bool
    pushed: bool
    message: str
    details: str = ""


def build_public_material_payload(db: MaterialDatabase) -> dict[str, Any]:
    _ensure_default_official_site_content(db)
    materials = [_with_rmb(db, row) for row in db.list_materials("", limit=1_000_000)]
    source_items = db.search_sources("", limit=1_000_000)
    recipes = []
    for row in db.list_recipes():
        full = db.get_recipe(int(row["id"]))
        if full:
            recipes.append(_with_material_prices(db, full))
    upgrades = []
    for row in db.list_upgrade_steps():
        full = db.get_upgrade_step(int(row["id"]))
        if full:
            upgrades.append(_with_material_prices(db, full))
    aliases = db.list_aliases("")
    source_names = db.all_source_names(limit=50_000)
    item_names = db.all_item_names(limit=50_000)
    return {
        "version": 1,
        "exported_at": now_iso(),
        "site": build_official_site_payload(db),
        "diamond_per_rmb": db.diamond_per_rmb(),
        "counts": {
            "materials": len(materials),
            "source_items": len(source_items),
            "recipes": len(recipes),
            "upgrade_steps": len(upgrades),
            "aliases": len(aliases),
        },
        "item_names": item_names,
        "source_names": source_names,
        "materials": materials,
        "source_items": source_items,
        "recipes": recipes,
        "upgrades": upgrades,
        "aliases": aliases,
    }


def export_material_site(workspace: str | Path, output_dir: str | Path, *, zip_path: str | Path | None = None) -> Path:
    db = MaterialDatabase(workspace)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    for file_name in PUBLIC_SITE_FILES:
        shutil.copy2(PUBLIC_SITE_ROOT / file_name, target / file_name)
    for dir_name in PUBLIC_SITE_ASSET_DIRS:
        source_dir = PUBLIC_SITE_ROOT / dir_name
        if source_dir.exists():
            target_dir = target / dir_name
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.copytree(source_dir, target_dir)
    payload = build_public_material_payload(db)
    (target / DATA_FILE_NAME).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if zip_path:
        write_site_zip(target, zip_path)
    return target


def build_official_site_payload(db: MaterialDatabase) -> dict[str, Any]:
    grouped: dict[str, Any] = {}
    for row in db.list_official_site_content():
        section = str(row.get("section") or "")
        item = {
            "key": row.get("item_key"),
            "title": row.get("title") or "",
            "subtitle": row.get("subtitle") or "",
            "body": row.get("body") or "",
            "url": row.get("url") or "",
            "badge": row.get("badge") or "",
            "meta": row.get("meta") or {},
        }
        if section == "meta":
            grouped["meta"] = item
        else:
            grouped.setdefault(section, []).append(item)
    grouped.setdefault("meta", DEFAULT_OFFICIAL_SITE_CONTENT[0])
    grouped.setdefault("stats", [])
    grouped.setdefault("announcements", [])
    grouped.setdefault("features", [])
    grouped.setdefault("links", [])
    grouped.setdefault("boards", [])
    return grouped


def _ensure_default_official_site_content(db: MaterialDatabase) -> None:
    seed_version = db.get_setting("official_site_seed_version")
    if db.official_site_content_count() == 0 or seed_version != DEFAULT_OFFICIAL_SITE_CONTENT_VERSION:
        db.upsert_official_site_content(DEFAULT_OFFICIAL_SITE_CONTENT)
        db.set_setting("official_site_seed_version", DEFAULT_OFFICIAL_SITE_CONTENT_VERSION)


def sync_material_site(
    workspace: str | Path,
    output_dir: str | Path,
    *,
    push: bool = True,
    zip_path: str | Path | None = None,
    commit_message: str | None = None,
) -> MaterialSiteSyncResult:
    target = export_material_site(workspace, output_dir, zip_path=zip_path)
    if not _is_git_repo(target):
        return MaterialSiteSyncResult(
            output_dir=target,
            git_repo=False,
            changed=True,
            committed=False,
            pushed=False,
            message="已导出官方网站；同步目录不是 Git 仓库，未自动推送。",
        )

    status = _git(target, "status", "--porcelain", "--", *PUBLISH_FILES)
    if not status.stdout.strip():
        return MaterialSiteSyncResult(
            output_dir=target,
            git_repo=True,
            changed=False,
            committed=False,
            pushed=False,
            message="已导出官方网站；内容没有变化，无需推送。",
        )

    _git(target, "add", "--", *PUBLISH_FILES)
    message = commit_message or f"Update material site {now_iso()}"
    commit = _git(target, "commit", "-m", message, allow_failure=True)
    if commit.returncode != 0:
        combined = "\n".join(part for part in (commit.stdout.strip(), commit.stderr.strip()) if part)
        if "nothing to commit" in combined.lower():
            return MaterialSiteSyncResult(
                output_dir=target,
                git_repo=True,
                changed=False,
                committed=False,
                pushed=False,
                message="已导出官方网站；Git 没有可提交变化。",
                details=combined,
            )
        raise RuntimeError(f"Git 提交失败：\n{combined}")

    if not push:
        return MaterialSiteSyncResult(
            output_dir=target,
            git_repo=True,
            changed=True,
            committed=True,
            pushed=False,
            message="已导出并提交官方网站；未执行推送。",
            details=commit.stdout.strip(),
        )

    pushed = _git(target, "push", allow_failure=True, timeout=120)
    if pushed.returncode != 0 and "upstream" in (pushed.stderr + pushed.stdout).lower():
        pushed = _git(target, "push", "-u", "origin", "HEAD", allow_failure=True, timeout=120)
    if pushed.returncode != 0:
        combined = "\n".join(part for part in (pushed.stdout.strip(), pushed.stderr.strip()) if part)
        raise RuntimeError(f"Git 推送失败：\n{combined}")
    return MaterialSiteSyncResult(
        output_dir=target,
        git_repo=True,
        changed=True,
        committed=True,
        pushed=True,
        message="已导出、提交并推送官方网站。",
        details="\n".join(part for part in (commit.stdout.strip(), pushed.stdout.strip()) if part),
    )


def write_site_zip(site_dir: str | Path, zip_path: str | Path) -> Path:
    source = Path(site_dir)
    target = Path(zip_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source))
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出可公开部署的石器时代官方网站静态网页")
    parser.add_argument("--workspace", default=str(Path.cwd()), help="项目根目录，默认当前目录")
    parser.add_argument("--output", default="data/public_material_site", help="导出网页文件夹")
    parser.add_argument("--zip", dest="zip_path", default="", help="可选：同时生成 zip 发布包")
    parser.add_argument("--sync-git", action="store_true", help="导出后自动 Git 提交并推送")
    parser.add_argument("--no-push", action="store_true", help="配合 --sync-git，仅提交不推送")
    args = parser.parse_args(argv)
    if args.sync_git:
        result = sync_material_site(
            args.workspace,
            args.output,
            push=not args.no_push,
            zip_path=args.zip_path or None,
        )
        print(result.message)
        print(f"目录：{result.output_dir}")
        if result.details:
            print(result.details)
        return 0

    output = export_material_site(args.workspace, args.output, zip_path=args.zip_path or None)
    print(f"官方网站网页已导出：{output}")
    if args.zip_path:
        print(f"Zip 发布包：{args.zip_path}")
    print(f"上传整个文件夹即可公开访问，入口文件：{output / 'index.html'}")
    return 0


def _with_rmb(db: MaterialDatabase, row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    price = data.get("price_diamonds")
    if price not in {None, ""}:
        data["price_rmb"] = db.diamonds_to_rmb(float(price))
    return data


def _with_material_prices(db: MaterialDatabase, row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    materials = []
    for material in data.get("materials") or []:
        item = dict(material)
        price = db.get_price(str(item.get("material_name") or ""))
        if price:
            item["price_diamonds"] = price.get("price_diamonds")
            item["price_rmb"] = db.diamonds_to_rmb(float(price.get("price_diamonds") or 0))
        materials.append(item)
    data["materials"] = materials
    return data


def _is_git_repo(path: Path) -> bool:
    result = _git(path, "rev-parse", "--is-inside-work-tree", allow_failure=True)
    return result.returncode == 0 and result.stdout.strip() == "true"


def _git(
    cwd: Path,
    *args: str,
    allow_failure: bool = False,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0 and not allow_failure:
        combined = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        raise RuntimeError(f"Git 命令失败：git {' '.join(args)}\n{combined}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
