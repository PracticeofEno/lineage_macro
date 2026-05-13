from __future__ import annotations

import argparse
import sys
import tkinter as tk
from tkinter import messagebox, ttk

import win32gui


WindowInfo = tuple[int, str]


def list_visible_windows() -> list[WindowInfo]:
    windows: list[WindowInfo] = []

    def callback(hwnd: int, _extra) -> bool:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
        except win32gui.error:
            return True
        if title:
            windows.append((hwnd, title))
        return True

    try:
        win32gui.EnumWindows(callback, None)
    except win32gui.error:
        # Some non-interactive Windows sessions can fail enumeration even when
        # the same code works from the user's desktop. Keep the tool open and
        # let Refresh try again instead of crashing.
        return windows
    return windows


def find_windows(query: str, exact: bool) -> list[WindowInfo]:
    query_lower = query.casefold()
    matches: list[WindowInfo] = []
    for hwnd, title in list_visible_windows():
        title_lower = title.casefold()
        if exact:
            matched = title_lower == query_lower
        else:
            matched = query_lower in title_lower
        if matched:
            matches.append((hwnd, title))
    return matches


def set_window_title(hwnd: int, new_title: str) -> None:
    if not win32gui.IsWindow(hwnd):
        raise RuntimeError(f"window not found: hwnd={hwnd}")
    if not new_title:
        raise RuntimeError("new title is empty")
    win32gui.SetWindowText(hwnd, new_title)


def print_windows(windows: list[WindowInfo]) -> None:
    print(f"{'HWND':<12} Title")
    print("-" * 70)
    for hwnd, title in windows:
        print(f"{hwnd:<12} {title}")


def run_cli(args: argparse.Namespace) -> int:
    if args.list:
        print_windows(list_visible_windows())
        return 0

    if args.hwnd is not None:
        hwnd = args.hwnd
    elif args.match:
        matches = find_windows(args.match, args.exact)
        if not matches:
            print(f"[title] window not found: {args.match!r}")
            return 1
        if len(matches) > 1 and not args.force_first:
            print(f"[title] multiple windows found for {args.match!r}:")
            print_windows(matches)
            print("[title] rerun with --force-first or use --hwnd")
            return 1
        hwnd = matches[0][0]
    else:
        print("[title] use --list, --hwnd, --match, or run without options for GUI")
        return 1

    try:
        old_title = win32gui.GetWindowText(hwnd)
        set_window_title(hwnd, args.title)
        current_title = win32gui.GetWindowText(hwnd)
    except RuntimeError as exc:
        print(f"[title] error: {exc}")
        return 1

    print(f"[title] hwnd={hwnd}")
    print(f"[title] old={old_title!r}")
    print(f"[title] new={current_title!r}")
    return 0


class WindowTitleChanger(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Window Title Changer")
        self.geometry("820x520")
        self.minsize(680, 420)

        self._windows: list[WindowInfo] = []
        self._selected_hwnd: int | None = None

        self._build_ui()
        self.refresh_windows()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self, padding=(10, 10, 10, 6))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Filter").grid(row=0, column=0, sticky="w")
        self.filter_var = tk.StringVar()
        filter_entry = ttk.Entry(top, textvariable=self.filter_var)
        filter_entry.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        filter_entry.bind("<KeyRelease>", lambda _event: self.refresh_tree())

        ttk.Button(top, text="Refresh", command=self.refresh_windows).grid(row=0, column=2)

        table_frame = ttk.Frame(self, padding=(10, 0, 10, 8))
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(table_frame, columns=("hwnd", "title"), show="headings", selectmode="browse")
        self.tree.heading("hwnd", text="HWND")
        self.tree.heading("title", text="Title")
        self.tree.column("hwnd", width=110, minwidth=90, stretch=False, anchor="w")
        self.tree.column("title", width=580, minwidth=240, stretch=True, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", self.focus_title_entry)

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        bottom = ttk.Frame(self, padding=(10, 0, 10, 10))
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.columnconfigure(1, weight=1)

        ttk.Label(bottom, text="Selected").grid(row=0, column=0, sticky="w")
        self.selected_var = tk.StringVar(value="No window selected")
        ttk.Label(bottom, textvariable=self.selected_var).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Label(bottom, text="New title").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.title_var = tk.StringVar()
        self.title_entry = ttk.Entry(bottom, textvariable=self.title_var)
        self.title_entry.grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=(8, 0))
        self.title_entry.bind("<Return>", lambda _event: self.apply_title())

        ttk.Button(bottom, text="Set Title", command=self.apply_title).grid(row=1, column=2, pady=(8, 0))

        quick = ttk.Frame(bottom)
        quick.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
        for value in ("server", "client", "client2", "client3", "Lineage Classic"):
            ttk.Button(quick, text=value, command=lambda title=value: self.set_quick_title(title)).pack(
                side="left",
                padx=(0, 6),
            )

    def refresh_windows(self) -> None:
        selected_hwnd = self._selected_hwnd
        self._windows = list_visible_windows()
        self.refresh_tree()

        if selected_hwnd is not None:
            for item_id in self.tree.get_children():
                if int(self.tree.item(item_id, "values")[0]) == selected_hwnd:
                    self.tree.selection_set(item_id)
                    self.tree.see(item_id)
                    break

    def refresh_tree(self) -> None:
        query = self.filter_var.get().casefold().strip()
        self.tree.delete(*self.tree.get_children())

        for hwnd, title in self._windows:
            if query and query not in title.casefold() and query not in str(hwnd):
                continue
            self.tree.insert("", "end", values=(hwnd, title))

    def on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            self._selected_hwnd = None
            self.selected_var.set("No window selected")
            return

        hwnd_text, title = self.tree.item(selection[0], "values")
        self._selected_hwnd = int(hwnd_text)
        self.selected_var.set(f"{hwnd_text} - {title}")
        self.title_var.set(title)

    def focus_title_entry(self, _event=None) -> None:
        self.title_entry.focus_set()
        self.title_entry.selection_range(0, tk.END)

    def set_quick_title(self, title: str) -> None:
        self.title_var.set(title)
        self.apply_title()

    def apply_title(self) -> None:
        if self._selected_hwnd is None:
            messagebox.showwarning("Window Title Changer", "Select a window first.")
            return

        new_title = self.title_var.get().strip()
        if not new_title:
            messagebox.showwarning("Window Title Changer", "New title is empty.")
            return

        try:
            set_window_title(self._selected_hwnd, new_title)
        except RuntimeError as exc:
            messagebox.showerror("Window Title Changer", str(exc))
            self.refresh_windows()
            return

        self.refresh_windows()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List or change visible Windows window titles.")
    parser.add_argument("--list", action="store_true", help="List visible windows and exit.")
    parser.add_argument("--hwnd", type=int, help="Target window handle.")
    parser.add_argument("--match", help="Find a window by title text.")
    parser.add_argument("--exact", action="store_true", help="Require exact title match with --match.")
    parser.add_argument("--force-first", action="store_true", help="Use the first match when several windows match.")
    parser.add_argument("--title", help="New title to set.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if len(sys.argv[1:] if argv is None else argv) == 0:
        app = WindowTitleChanger()
        app.mainloop()
        return 0

    if not args.list and not args.title:
        print("[title] --title is required when changing a window title")
        return 1

    return run_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
