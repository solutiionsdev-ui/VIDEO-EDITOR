"""The `reel` command.

    reel run <job> --client <slug>        run every stage built so far
    reel intake|audio|transcribe|takes|cuts|face <job> [--client <slug>] [--show]
    reel show <job> <stage>               print an existing result again
    reel check <job>                      quality check of the latest render (fixes and re-renders what it can)
    reel revise <job> "beat 4: ..."       apply client comments, log the rules, re-render changed beats
    reel status <job>                     which stages ran, how long they took
    reel view <job>                       review page at http://localhost:8765
    reel doctor                           check the tools are in place
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from rich.table import Table

from . import __version__
from .job import RUN_DEFAULT_TO, STAGES, Job, JobError, console
from .profile import load_profile
from .stages import audio, beats, check, cuts, face, intake, render, revise, stills, takes, transcribe

STAGE_MODULES = {
    "intake": intake,
    "audio": audio,
    "transcribe": transcribe,
    "takes": takes,
    "cuts": cuts,
    "face": face,
    "beats": beats,
    "stills": stills,
    "render": render,
    "check": check,
}
NEEDS = {
    "audio": "intake.json",
    "transcribe": "audio/index.json",
    "takes": "transcript.json",
    "cuts": "takes.json",
    "face": "cuts.json",
    "beats": "face.json",
    "stills": "beats.json",
    "render": "beats.json",
    "check": "beats.json",
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    try:
        return args.func(args) or 0
    except beats.NeedsOperator as e:
        console.print(f"[yellow bold]Needs Claude:[/] {e}")
        return beats.NEEDS_OPERATOR
    except JobError as e:
        console.print(f"[red bold]Error:[/] {e}")
        return 2
    except KeyboardInterrupt:
        console.print("\n[yellow]stopped[/]")
        return 130


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reel", description="Studiio Reel Editor engine")
    p.add_argument("--version", action="version", version=f"reel {__version__}")
    sub = p.add_subparsers(dest="command")

    run = sub.add_parser("run", help="run every stage (intake → face) on a job folder")
    run.add_argument("job")
    run.add_argument("--client", help="client slug (clients/<slug>/profile.yaml) or a path to a profile")
    run.add_argument("--from", dest="from_stage", choices=STAGES, help="start at this stage")
    run.add_argument("--to", dest="to_stage", choices=STAGES, default=RUN_DEFAULT_TO,
                     help=f"stop after this stage (default {RUN_DEFAULT_TO}; the render is a separate approval)")
    run.add_argument("--force", action="store_true", help="continue even if intake found blockers")
    run.add_argument("--no-preview", action="store_true", help="skip the preview videos (faster)")
    run.set_defaults(func=cmd_run)

    for name, module in STAGE_MODULES.items():
        sp = sub.add_parser(name, help=(module.__doc__ or "").strip().splitlines()[0])
        sp.add_argument("job")
        sp.add_argument("--client")
        sp.add_argument("--show", action="store_true", help="print the result in detail")
        if name in ("cuts", "face"):
            sp.add_argument("--no-preview", action="store_true")
        if name == "beats":
            sp.add_argument("--planner", choices=["auto", "api", "manual"], default=None,
                            help="auto: Claude API if credentials exist, else Claude Code writes the plan")
            sp.add_argument("--force", action="store_true", help="plan again even if beats-plan.json exists")
        if name == "render":
            sp.add_argument("--workers", type=int, default=None, help="parallel browser renderers (default: cores/3, max 4)")
            sp.add_argument("--no-check", action="store_true", help="skip the automatic quality check")
        if name == "check":
            sp.add_argument("--version", type=int, default=None, help="render version to check (default: latest)")
            sp.add_argument("--no-fix", action="store_true", help="report only; don't fix and re-render")
        sp.set_defaults(func=cmd_stage, stage=name)

    rev = sub.add_parser("revise", help="apply client comments: re-plan the beats they touch, log the rules, re-render")
    rev.add_argument("job")
    rev.add_argument("comments", nargs="*", help='e.g. "beat 4: captions bigger" "0:32 no graphic here"')
    rev.add_argument("--file", help="a text file with one comment per line")
    rev.add_argument("--client")
    rev.add_argument("--no-render", action="store_true", help="apply the changes but don't render")
    rev.add_argument("--show", action="store_true")
    rev.set_defaults(func=cmd_revise)

    show = sub.add_parser("show", help="print an existing result (intake, transcript, takes, cuts, face, beats, stills, render, check)")
    show.add_argument("job")
    show.add_argument("stage", choices=["intake", "transcript", "transcribe", "takes", "cuts", "face", "beats", "stills", "render", "check"])
    show.set_defaults(func=cmd_show)

    status = sub.add_parser("status", help="which stages have run on a job")
    status.add_argument("job")
    status.set_defaults(func=cmd_status)

    doctor = sub.add_parser("doctor", help="check that ffmpeg, the models and the libraries work")
    doctor.set_defaults(func=cmd_doctor)

    view = sub.add_parser("view", help="open a local review page for a job (results + preview videos)")
    view.add_argument("job")
    view.add_argument("--port", type=int, default=8765)
    view.add_argument("--no-open", action="store_true", help="don't open the browser automatically")
    view.set_defaults(func=cmd_view)
    return p


def cmd_view(args) -> int:
    from .review import serve

    serve(Job(Path(args.job)), port=args.port, open_browser=not args.no_open)
    return 0


def _open(args) -> tuple[Job, dict]:
    job = Job(Path(args.job))
    client = getattr(args, "client", None) or job.state.get("client")
    profile = load_profile(client)
    if client and job.state.get("client") != client:
        job.set_client(client)
    name = profile.get("client", {}).get("name") or client or "no client profile (engine defaults)"
    console.print(f"[dim]job {job.name} · client: {name}[/]")
    return job, profile


def _run_one(job: Job, profile: dict, stage: str, show: bool, preview: bool = True,
             planner: str | None = None, force: bool = False, workers: int | None = None,
             no_check: bool = False, version: int | None = None, no_fix: bool = False):
    need = NEEDS.get(stage)
    if need and not job.has(need):
        raise JobError(f"'{stage}' needs {need}. Run the earlier stages first (or `reel run {job.path}`).")
    module = STAGE_MODULES[stage]
    kwargs = {}
    if stage in ("transcribe", "takes", "cuts", "face", "beats", "stills", "render", "check"):
        kwargs["show"] = show
    if stage in ("cuts", "face"):
        kwargs["preview_video"] = preview
    if stage == "beats":
        kwargs["planner"] = planner or str(profile.get("ai", {}).get("planner", "auto"))
        kwargs["force"] = force
    if stage == "render":
        if workers:
            kwargs["workers"] = workers
        kwargs["run_check"] = not no_check
    if stage == "check":
        kwargs["version"] = version
        kwargs["fix"] = not no_fix
    return job.run_stage(stage, module.run, profile, **kwargs)


def cmd_stage(args) -> int:
    job, profile = _open(args)
    preview = not getattr(args, "no_preview", False)
    result = _run_one(job, profile, args.stage, args.show, preview,
                      planner=getattr(args, "planner", None), force=getattr(args, "force", False),
                      workers=getattr(args, "workers", None), no_check=getattr(args, "no_check", False),
                      version=getattr(args, "version", None), no_fix=getattr(args, "no_fix", False))
    if args.stage == "intake" and result["status"] == "blocked":
        return 3
    if args.stage == "check" and result["status"] == "fail":
        return 5
    return 0


def cmd_revise(args) -> int:
    job, profile = _open(args)
    comments = list(args.comments or [])
    if args.file:
        comments += [line.strip() for line in Path(args.file).read_text(encoding="utf-8").splitlines() if line.strip()]
    result = job.run_stage("revise", revise.run, profile, comments, show=args.show, render=not args.no_render)
    return 5 if result.get("check") == "fail" else 0


def cmd_run(args) -> int:
    """Run the stages one after another, each in its own process.

    Separate processes keep the transcriber's and the face tracker's libraries
    apart (they bundle clashing copies of ffmpeg) and free the memory of the
    Whisper model before the next stage starts.
    """
    job, profile = _open(args)
    client = getattr(args, "client", None) or job.state.get("client")
    start = STAGES.index(args.from_stage) if args.from_stage else 0
    stop = STAGES.index(args.to_stage)
    if stop < start:
        raise JobError("--to comes before --from")
    for stage in STAGES[start:stop + 1]:
        cmd = [sys.executable, "-m", "engine.cli", stage, str(job.path)]
        if client:
            cmd += ["--client", client]
        if args.no_preview and stage in ("cuts", "face"):
            cmd.append("--no-preview")
        code = subprocess.call(cmd)
        if stage == "intake" and code == 3:
            if args.force:
                console.print("[yellow]continuing despite intake blockers (--force)[/]")
            else:
                console.print("\n[red bold]Stopped:[/] intake found blockers. Fix them, or re-run with --force.")
                return 3
        elif stage == "beats" and code == beats.NEEDS_OPERATOR:
            console.print("\n[yellow bold]Paused:[/] the beat plan is waiting for Claude (see above). "
                          "Everything before it is done.")
            break
        elif code != 0:
            console.print(f"\n[red bold]Stopped:[/] stage '{stage}' failed (see above).")
            return code
    console.rule("[bold]result")
    if job.has("takes.json"):
        takes.report(job, job.read_json("takes.json"))
    if job.has("cuts.json"):
        cuts.report(job, job.read_json("cuts.json"))
    if job.has("face.json"):
        face.report(job, job.read_json("face.json"))
    if job.has("beats.json"):
        beats.report(job, job.read_json("beats.json"))
    cmd_status(args)
    return 0


def cmd_show(args) -> int:
    job = Job(Path(args.job))
    stage = {"transcribe": "transcript"}.get(args.stage, args.stage)
    files = {"intake": ("intake.json", intake), "transcript": ("transcript.json", transcribe),
             "takes": ("takes.json", takes), "cuts": ("cuts.json", cuts), "face": ("face.json", face),
             "beats": ("beats.json", beats), "stills": ("stills/index.json", stills)}
    if stage in ("render", "check"):
        version = check.latest_version(job)
        if version is None:
            raise JobError("No render yet. Run `reel render <job>`.")
        if stage == "render":
            render.report(job, job.read_json(f"renders/v{version}.json"))
        else:
            if not job.has(f"renders/v{version}-check.json"):
                raise JobError(f"v{version} has not been checked. Run `reel check <job>`.")
            check.report(job, job.read_json(f"renders/v{version}-check.json"))
        return 0
    name, module = files[stage]
    module.report(job, job.read_json(name))
    return 0


def cmd_status(args) -> int:
    job = Job(Path(args.job))
    table = Table(title=f"job {job.name}", show_header=True, header_style="bold", box=None)
    table.add_column("stage")
    table.add_column("status")
    table.add_column("time", justify="right")
    table.add_column("result")
    outputs = {"intake": "intake.json", "audio": "audio/index.json", "transcribe": "transcript.json",
               "takes": "takes.json", "cuts": "cuts.json", "face": "face.json", "beats": "beats.json",
               "stills": "stills/index.json", "render": "renders/v1.mp4", "check": "renders/v1-check.json"}
    for stage in STAGES:
        entry = job.state.get("stages", {}).get(stage, {})
        status = entry.get("status", "—")
        colour = {"done": "green", "failed": "red", "running": "yellow"}.get(status, "dim")
        secs = f"{entry['seconds']}s" if entry.get("seconds") is not None else ""
        table.add_row(stage, f"[{colour}]{status}[/]", secs, outputs[stage] if job.has(outputs[stage]) else "")
    console.print(table)
    return 0


def cmd_doctor(args) -> int:
    ok = True
    try:
        from .media import ffmpeg_exe
        import imageio_ffmpeg
        console.print(f"[green]✔[/] ffmpeg {imageio_ffmpeg.get_ffmpeg_version()} at {ffmpeg_exe()}")
    except Exception as e:  # noqa: BLE001
        ok = False
        console.print(f"[red]✖[/] ffmpeg: {e}")
    # Each library is imported in a fresh process: cv2 and faster_whisper must not share one.
    for mod in ("numpy", "yaml", "cv2", "mediapipe", "faster_whisper"):
        proc = subprocess.run([sys.executable, "-c", f"import {mod}"], capture_output=True, text=True)
        if proc.returncode == 0:
            console.print(f"[green]✔[/] {mod}")
        else:
            ok = False
            console.print(f"[red]✖[/] {mod}: {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else 'import failed'}")
    if face.MODEL.is_file():
        console.print(f"[green]✔[/] face model {face.MODEL.name}")
    else:
        ok = False
        console.print(f"[red]✖[/] face model missing at {face.MODEL}")
    proc = subprocess.run([sys.executable, "-c",
                           "from playwright.sync_api import sync_playwright\n"
                           "with sync_playwright() as p:\n    b = p.chromium.launch(headless=True); print(b.version); b.close()"],
                          capture_output=True, text=True)
    if proc.returncode == 0:
        console.print(f"[green]✔[/] Chromium for the renderer ({proc.stdout.strip()})")
    else:
        ok = False
        console.print("[red]✖[/] Chromium for the renderer is missing — run: .venv/bin/playwright install chromium")
    cache = Path.home() / ".cache" / "huggingface" / "hub"
    models = sorted(p.name for p in cache.glob("models--*whisper*")) if cache.is_dir() else []
    if models:
        console.print(f"[green]✔[/] whisper models downloaded: {', '.join(models)}")
    else:
        console.print("[yellow]![/] no whisper model downloaded yet (happens automatically on first transcribe)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
