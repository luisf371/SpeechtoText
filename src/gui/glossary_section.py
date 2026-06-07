"""Glossary management section — CustomTkinter version."""

import tkinter as tk
from tkinter import messagebox
from typing import Callable
import customtkinter as ctk

C_WIN     = "#131519"
C_INPUT   = "#0e1013"
C_SURFACE = "#1b1e24"
C_SURFACE2 = "#23272f"
C_BORDER  = "#2a2f38"
C_TEXT    = "#e7e9ec"
C_TEXT2   = "#9ba2ab"
C_TEXT3   = "#6a717b"
C_ACCENT  = "#f5a524"
C_ACCENT2 = "#ffb454"

FONT_SM   = ("Segoe UI", 13)
FONT_BOLD = ("Segoe UI", 14, "bold")


class GlossaryTermDialog:
    """Modal dialog for adding/editing a glossary term."""

    def __init__(self, parent, title: str, initial_value: str = ""):
        self.result: str | None = None
        self._dialog = ctk.CTkToplevel(parent)
        self._dialog.title(title)
        self._dialog.geometry("360x160")
        self._dialog.resizable(False, False)
        self._dialog.configure(fg_color="#131519")
        self._dialog.transient(parent)
        self._dialog.grab_set()

        pad = ctk.CTkFrame(self._dialog, fg_color="transparent", corner_radius=0)
        pad.pack(fill="both", expand=True, padx=20, pady=18)

        ctk.CTkLabel(
            pad, text="Enter glossary term:", font=FONT_SM,
            text_color="#9ba2ab", fg_color="transparent", anchor="w",
        ).pack(anchor="w", pady=(0, 6))

        self._var = tk.StringVar(value=initial_value)
        self._entry = ctk.CTkEntry(
            pad, textvariable=self._var, font=("Segoe UI", 13),
            fg_color="#0e1013", border_color="#2a2f38", text_color="#e7e9ec",
            height=33, corner_radius=7,
        )
        self._entry.pack(fill="x", pady=(0, 14))
        self._entry.focus()
        self._entry.select_range(0, tk.END)

        btns = ctk.CTkFrame(pad, fg_color="transparent", corner_radius=0)
        btns.pack(fill="x")
        ctk.CTkButton(
            btns, text="OK", command=self._ok,
            height=31, corner_radius=6, font=FONT_SM,
            fg_color="#f5a524", text_color="#1a1205", hover_color="#ffb454",
        ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(
            btns, text="Cancel", command=self._cancel,
            height=31, corner_radius=6, font=FONT_SM,
            fg_color="#1b1e24", text_color="#9ba2ab",
            hover_color="#23272f", border_width=1, border_color="#2a2f38",
        ).pack(side="right")

        self._dialog.bind("<Return>", lambda _e: self._ok())
        self._dialog.bind("<Escape>", lambda _e: self._cancel())

    def show(self) -> str | None:
        self._dialog.wait_window()
        return self.result

    def _ok(self):
        self.result = self._var.get()
        self._dialog.destroy()

    def _cancel(self):
        self._dialog.destroy()


class GlossarySection:
    """Glossary list with search, add, edit, delete."""

    def __init__(
        self,
        parent,
        root,
        initial_terms: list[str],
        on_change: Callable[[], None] | None = None,
    ):
        self.root = root
        self.on_change = on_change
        self.glossary_terms = list(initial_terms)

        self.frame = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        self.frame.pack(fill="both", expand=True)

        self.glossary_search_var = tk.StringVar()
        self.glossary_listbox: tk.Listbox | None = None

        self._create_widgets()

    def _create_widgets(self):
        f = self.frame

        # Search
        search_row = ctk.CTkFrame(f, fg_color="transparent", corner_radius=0)
        search_row.pack(fill="x", pady=(0, 8))
        ctk.CTkEntry(
            search_row, textvariable=self.glossary_search_var,
            placeholder_text="Filter terms…",
            font=("Segoe UI", 13), fg_color=C_INPUT,
            border_color=C_BORDER, text_color=C_TEXT, height=33, corner_radius=7,
        ).pack(fill="x")
        self.glossary_search_var.trace("w", self._filter_glossary_list)

        # Listbox (tk.Listbox with dark colors — CTk has no native listbox)
        lb_frame = ctk.CTkFrame(f, fg_color=C_INPUT, corner_radius=7, border_width=1,
                                border_color=C_BORDER)
        lb_frame.pack(fill="x", pady=(0, 8))

        self.glossary_listbox = tk.Listbox(
            lb_frame,
            height=6,
            bg=C_INPUT, fg=C_TEXT,
            selectbackground="#1d1a0d", selectforeground=C_ACCENT2,
            borderwidth=0, highlightthickness=0,
            activestyle="none",
            font=("Segoe UI", 13),
        )
        sb = ctk.CTkScrollbar(lb_frame, command=self.glossary_listbox.yview,
                              fg_color=C_INPUT, button_color=C_SURFACE2,
                              button_hover_color=C_TEXT3)
        self.glossary_listbox.configure(yscrollcommand=sb.set)
        self.glossary_listbox.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        sb.pack(side="right", fill="y", pady=4)

        # Centered empty-state placeholder (shown when no terms are present)
        self._empty_label = ctk.CTkLabel(
            lb_frame,
            text="No terms yet\nAdd acronyms or jargon the model keeps missing",
            font=FONT_SM, text_color=C_TEXT3, fg_color=C_INPUT, justify="center",
        )

        # Buttons
        btn_row = ctk.CTkFrame(f, fg_color="transparent", corner_radius=0)
        btn_row.pack(anchor="w")
        for text, cmd in [
            ("Add term", self._add_term),
            ("Edit",     self._edit_term),
            ("Delete",   self._delete_term),
        ]:
            style = dict(fg_color=C_ACCENT, text_color="#1a1205", hover_color=C_ACCENT2) \
                if text == "Add term" else \
                dict(fg_color=C_SURFACE, text_color=C_TEXT2, hover_color=C_SURFACE2,
                     border_width=1, border_color=C_BORDER)
            ctk.CTkButton(
                btn_row, text=text, command=cmd,
                height=31, corner_radius=6, font=FONT_SM, **style,
            ).pack(side="left", padx=(0, 6))

        self._refresh_list()

    def _filter_glossary_list(self, *_args):
        search = self.glossary_search_var.get().lower()
        self.glossary_listbox.delete(0, tk.END)
        for term in self.glossary_terms:
            if search in term.lower():
                self.glossary_listbox.insert(tk.END, term)
        self._update_empty_state()

    def _refresh_list(self):
        if not self.glossary_listbox:
            return
        self.glossary_listbox.delete(0, tk.END)
        for term in sorted(self.glossary_terms, key=str.lower):
            self.glossary_listbox.insert(tk.END, term)
        self._update_empty_state()

    def _update_empty_state(self):
        empty_label = getattr(self, "_empty_label", None)
        if not empty_label or not self.glossary_listbox:
            return
        if self.glossary_listbox.size() == 0:
            empty_label.place(relx=0.5, rely=0.5, anchor="center")
        else:
            empty_label.place_forget()

    def _add_term(self):
        term = GlossaryTermDialog(self.root, "Add Glossary Term").show()
        if term and term.strip():
            term = term.strip()
            if term not in self.glossary_terms:
                self.glossary_terms.append(term)
                self._refresh_list()
                if self.on_change:
                    self.on_change()
            else:
                messagebox.showinfo("Duplicate Term", f"'{term}' is already in the glossary.")

    def _edit_term(self):
        sel = self.glossary_listbox.curselection()
        if not sel:
            messagebox.showinfo("No Selection", "Please select a term to edit.")
            return
        current = self.glossary_listbox.get(sel[0])
        new_term = GlossaryTermDialog(self.root, "Edit Glossary Term", current).show()
        if new_term and new_term.strip() and new_term.strip() != current:
            new_term = new_term.strip()
            if new_term not in self.glossary_terms:
                self.glossary_terms[self.glossary_terms.index(current)] = new_term
                self._refresh_list()
                if self.on_change:
                    self.on_change()
            else:
                messagebox.showinfo("Duplicate Term", f"'{new_term}' is already in the glossary.")

    def _delete_term(self):
        sel = self.glossary_listbox.curselection()
        if not sel:
            messagebox.showinfo("No Selection", "Please select a term to delete.")
            return
        term = self.glossary_listbox.get(sel[0])
        if messagebox.askyesno("Confirm Delete", f"Delete '{term}'?"):
            self.glossary_terms.remove(term)
            self._refresh_list()
            if self.on_change:
                self.on_change()

    def get_terms(self) -> list[str]:
        return list(self.glossary_terms)

    def set_terms(self, terms: list[str]):
        self.glossary_terms = list(terms)
        self._refresh_list()
