from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from . import __version__
from .pipeline import Pipeline, PipelineError
from .sites import ADAPTERS, get_adapter
from .storage import Storage


def _date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日期必须为 YYYY-MM-DD") from exc
    return value


def _sources(values: list[str]) -> list[str]:
    expanded = list(ADAPTERS) if "all" in values else values
    return list(dict.fromkeys(expanded))


def _add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source",
        action="append",
        choices=[*ADAPTERS, "all"],
        required=True,
        help="来源代码；可重复指定，或使用 all",
    )
    parser.add_argument("--since", type=_date, help="仅处理该日期及之后的条目（YYYY-MM-DD）")
    parser.add_argument("--output", type=Path, default=Path("data"), help="本地归档目录（默认 ./data）")
    parser.add_argument("--delay", type=float, default=1.0, help="相邻请求最小间隔秒数（默认 1.0）")
    parser.add_argument("--timeout", type=float, default=30.0, help="单次请求超时秒数（默认 30）")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="policy-harvester",
        description="按来源增量归档中国部委公开政策正文与附件。",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    update = commands.add_parser("update", help="增量保存正文、附件和清单")
    _add_source_arguments(update)
    update.add_argument("--dry-run", action="store_true", help="只扫描索引，不写入归档")

    preview = commands.add_parser("preview", help="扫描索引并输出候选 JSON，不写入归档")
    _add_source_arguments(preview)

    audit = commands.add_parser("audit", help="离线检查清单状态和本地缺失文件")
    audit.add_argument("--output", type=Path, default=Path("data"), help="本地归档目录（默认 ./data）")

    serve = commands.add_parser("serve", help="启动仅监听本机的网页界面")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址（默认且推荐 127.0.0.1）")
    serve.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    serve.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    return parser


def _pipeline(source: str, storage: Storage | None, args: argparse.Namespace) -> Pipeline:
    return Pipeline(
        storage,
        get_adapter(source),
        delay=args.delay,
        timeout=args.timeout,
    )


def _run_update(args: argparse.Namespace) -> int:
    results: dict[str, dict[str, int | bool]] = {}

    def run(storage: Storage | None) -> None:
        for source in _sources(args.source):
            print(f"[{source}] 扫描配置栏目…", file=sys.stderr, flush=True)
            stats = _pipeline(source, storage, args).update(args.since, dry_run=args.dry_run)
            results[source] = asdict(stats)
            print(
                f"[{source}] 发现 {stats.indexed}；正文新增 {stats.documents_downloaded}；"
                f"附件新增 {stats.attachments_downloaded}；跳过 {stats.documents_skipped}",
                file=sys.stderr,
                flush=True,
            )

    if args.dry_run:
        run(None)
    else:
        with Storage(args.output) as storage:
            run(storage)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def _run_preview(args: argparse.Namespace) -> int:
    results: dict[str, list[dict[str, str]]] = {}
    for source in _sources(args.source):
        adapter = get_adapter(source)
        pipeline = Pipeline(None, adapter, delay=args.delay, timeout=args.timeout)
        policies = pipeline.preview(args.since)
        results[source] = [asdict(policy) for policy in policies]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def _run_audit(args: argparse.Namespace) -> int:
    with Storage(args.output) as storage:
        report = storage.audit()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    has_gaps = any(
        report[key]
        for key in (
            "policies_pending",
            "policies_failed",
            "attachments_pending",
            "attachments_failed",
            "missing_content_files",
            "missing_attachment_files",
        )
    )
    return 2 if has_gaps else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "delay", 1) < 0.2:
        parser.error("--delay 不能小于 0.2 秒")
    if getattr(args, "timeout", 1) <= 0:
        parser.error("--timeout 必须大于 0")
    try:
        if args.command == "update":
            return _run_update(args)
        if args.command == "preview":
            return _run_preview(args)
        if args.command == "audit":
            return _run_audit(args)
        if args.command == "serve":
            from .webapp import serve

            return serve(args.host, args.port, open_browser=not args.no_browser)
    except (PipelineError, OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    parser.error(f"未知命令：{args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
