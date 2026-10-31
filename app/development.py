import argparse
import os
import runpy
import signal
import socket
import subprocess
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from types import FrameType

from watchfiles import PythonFilter, watch

from app.core.constants import DEVELOPMENT, LocalService
from app.infrastructure.process_job import WindowsProcessJob


@dataclass(frozen=True, slots=True, kw_only=True)
class ServiceCommand:
    service: LocalService
    arguments: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ServiceProcess:
    service: LocalService
    process: subprocess.Popen[bytes]
    job: WindowsProcessJob | None


def relay_interrupt(signum: int, frame: FrameType | None) -> None:
    signal.raise_signal(signal.SIGINT)


def prepare_service() -> bool:
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, relay_interrupt)
    return sys.stdin.buffer.read(len(DEVELOPMENT.start_token)) == DEVELOPMENT.start_token


def run_service(service: LocalService) -> int:
    try:
        if not prepare_service():
            return 1
        if service == LocalService.WEB:
            import uvicorn

            uvicorn.run(
                DEVELOPMENT.web_app,
                host=DEVELOPMENT.host,
                port=DEVELOPMENT.port,
                access_log=False,
            )
        else:
            sys.argv = [DEVELOPMENT.worker_module]
            runpy.run_module(DEVELOPMENT.worker_module, run_name="__main__")
    except KeyboardInterrupt:
        return 0
    return 0


def stop_group(process: subprocess.Popen[bytes], *, force: bool = False) -> None:
    try:
        if sys.platform == "win32":
            if not force:
                process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError as error:
        if process.poll() is None:
            print(f"Не удалось передать сигнал процессу {process.pid}: {error}", file=sys.stderr, flush=True)


def stop_services(children: list[ServiceProcess]) -> None:
    for child in children:
        stop_group(child.process)
    deadline = monotonic() + DEVELOPMENT.shutdown_seconds
    while any(child.process.poll() is None for child in children) and monotonic() < deadline:
        sleep(DEVELOPMENT.poll_seconds)
    remaining = [child for child in children if child.process.poll() is None]
    if remaining:
        print("Время ожидания истекло. Завершаю оставшиеся процессы запуска.", file=sys.stderr, flush=True)
    for child in children:
        if child.job is not None:
            child.job.close()
        else:
            stop_group(child.process, force=True)
    for child in children:
        child.process.wait(timeout=DEVELOPMENT.force_wait_seconds)


def service_environment(service: LocalService) -> dict[str, str]:
    environment = os.environ.copy()
    if log_file := environment.get("CHECKSTOCK_LOG_FILE", "").strip():
        filename = "app.log" if service == LocalService.WEB else "scheduler.log"
        environment["CHECKSTOCK_LOG_FILE"] = str(Path(log_file).with_name(filename))
    return environment


def start_service(command: ServiceCommand, root: Path) -> ServiceProcess:
    job = WindowsProcessJob() if sys.platform == "win32" else None
    try:
        process = subprocess.Popen(
            command.arguments,
            cwd=root,
            env=service_environment(command.service),
            stdin=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            start_new_session=sys.platform != "win32",
        )
        try:
            if job is not None:
                job.attach(process.pid)
            if process.stdin is None:
                raise RuntimeError("Не удалось открыть канал запуска процесса")
            process.stdin.write(DEVELOPMENT.start_token)
            process.stdin.close()
        except BaseException:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            finally:
                process.kill()
                process.wait()
            raise
    except BaseException:
        if job is not None:
            job.close()
        raise
    print(f"Запущен {command.service}: PID {process.pid}", flush=True)
    return ServiceProcess(service=command.service, process=process, job=job)


def supervise(commands: tuple[ServiceCommand, ...], root: Path, stop: Event) -> int:
    children: list[ServiceProcess] = []
    try:
        for command in commands:
            if stop.is_set():
                return 0
            children.append(start_service(command, root))
        print(
            f"Сайт: http://{DEVELOPMENT.host}:{DEVELOPMENT.port} · Остановка обоих процессов: Ctrl+C",
            flush=True,
        )
        with closing(
            watch(
                root / "app",
                watch_filter=PythonFilter(),
                stop_event=stop,
                rust_timeout=DEVELOPMENT.watch_timeout_ms,
                debounce=DEVELOPMENT.reload_debounce_ms,
                yield_on_timeout=True,
                raise_interrupt=False,
            )
        ) as changes:
            for changed in changes:
                if stop.is_set():
                    return 0
                for child in children:
                    code = child.process.poll()
                    if code is not None:
                        print(
                            f"Процесс {child.service} завершился с кодом {code}.", file=sys.stderr, flush=True
                        )
                        return code if code > 0 else 1
                if changed:
                    for index, command in enumerate(commands):
                        if command.service == LocalService.WEB:
                            print("Изменились Python-файлы: перезапускаю сайт…", flush=True)
                            stop_services([children[index]])
                            if stop.is_set():
                                return 0
                            children[index] = start_service(command, root)
        return 0
    finally:
        if children:
            print("Останавливаю сайт и загрузчик…", flush=True)
        stop_services(children)


def check_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if sys.platform == "win32":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind((DEVELOPMENT.host, DEVELOPMENT.port))


def main() -> int:
    parser = argparse.ArgumentParser(description="Локальный запуск сайта и автоматических загрузок")
    parser.add_argument("--service", type=LocalService, choices=tuple(LocalService), help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.service is not None:
        return run_service(arguments.service)
    root = Path(__file__).resolve().parents[1]
    stop = Event()

    def request_stop(signum: int, frame: FrameType | None) -> None:
        stop.set()

    signals: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM)
    if sys.platform == "win32":
        signals += (signal.SIGBREAK,)
    previous = {sig: signal.signal(sig, request_stop) for sig in signals}
    try:
        try:
            check_port()
        except OSError as error:
            print(
                f"Не удалось открыть {DEVELOPMENT.host}:{DEVELOPMENT.port}. "
                f"Проверьте, не запущен ли сайт в другом терминале. Загрузчик не запущен. {error}",
                file=sys.stderr,
                flush=True,
            )
            return 1
        commands = tuple(
            ServiceCommand(
                service=service,
                arguments=(sys.executable, "-u", str(root / "run.py"), "--service", service.value),
            )
            for service in LocalService
        )
        return supervise(commands, root, stop)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Ошибка локального запуска: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
