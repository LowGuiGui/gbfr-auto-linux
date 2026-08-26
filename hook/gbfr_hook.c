#include <windows.h>
#include <stdio.h>
#include <string.h>

#define PIPE_NAME L"\\\\.\\pipe\\gbfr_hook"
#define BUF_SIZE  256

static HANDLE g_hPipe = INVALID_HANDLE_VALUE;
static HANDLE g_hThread = NULL;
static volatile BOOL g_running = TRUE;
static HANDLE g_hLog = INVALID_HANDLE_VALUE;

static void log_write(const char *msg) {
    if (g_hLog == INVALID_HANDLE_VALUE) return;
    SYSTEMTIME st;
    GetLocalTime(&st);
    char buf[512];
    int len = _snprintf_s(buf, sizeof(buf), _TRUNCATE,
        "[%02d:%02d:%02d.%03d] [pid=%u] %s\r\n",
        st.wHour, st.wMinute, st.wSecond, st.wMilliseconds,
        (unsigned)GetCurrentProcessId(), msg);
    DWORD written;
    WriteFile(g_hLog, buf, (DWORD)len, &written, NULL);
    FlushFileBuffers(g_hLog);
}

static void log_init(void) {
    wchar_t path[MAX_PATH];
    GetTempPathW(MAX_PATH, path);
    wcscat_s(path, MAX_PATH, L"gbfr_hook.log");
    g_hLog = CreateFileW(path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                          NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    log_write("======== DLL loaded ========");
}

/* =====================================================================
 * Focus spoofing  --  #45
 *
 * Measured 2026-08-25 (PLANNING.md 5.3): Windows does NOT gate XInput on
 * focus, the game reads the pad fine, and the game *pauses itself* when it
 * loses focus. So the job is not to deliver input differently -- it is to
 * stop the game noticing that it went to the background.
 *
 * Two mechanisms, because we do not know which one the game uses and the
 * cost of covering both is small:
 *
 *   1. IAT patch on GetForegroundWindow / GetActiveWindow / GetFocus, so a
 *      poll-style check ("am I in front?") answers yes.
 *   2. A window-proc subclass that swallows WM_KILLFOCUS,
 *      WM_ACTIVATE(WA_INACTIVE) and WM_ACTIVATEAPP(FALSE), so an
 *      event-style check never hears about it.
 *
 * SAFETY -- read before changing:
 *
 *   Spoofing is OFF until an explicit SPOOF_ON arrives. Injecting this DLL
 *   on its own changes nothing about how the game behaves, which is what it
 *   did before this feature existed. That property is deliberate: inject is
 *   already used for other things and must stay boring.
 *
 *   The counters run even while spoofing is off. That gives us the observer
 *   build for free and without risk: inject, send SPOOF_WATCH, alt-tab, and
 *   read SPOOF_STATS to learn which of the two mechanisms the game actually
 *   uses. If a hook shows zero calls it was never the answer.
 *
 *   SPOOF_WATCH is what makes that true for BOTH mechanisms. The IAT patches
 *   go in as soon as the pipe thread starts, so the poll counters run from the
 *   beginning -- but the subclass needs an hwnd, and until SPOOF_WATCH existed
 *   it only went in on SPOOF_ON. Observing without spoofing therefore pinned
 *   the three message counters at zero and could only ever answer "polls" or
 *   "nothing at all", whatever the game was really doing.
 *
 *   Everything is restored on SPOOF_OFF and on DLL unload.
 * ===================================================================== */

static HWND     g_spoofWnd    = NULL;
static volatile LONG g_spoofOn = 0;
static WNDPROC  g_origWndProc = NULL;
static HWND     g_subclassed  = NULL;

/* Diagnosis. Which mechanism does the game actually use? */

/* Installed-or-not, which is a different question from called-or-not. Without
 * these two, all-zero counters have two readings -- "the game does not use
 * these APIs" and "our hooks never went in" -- and they call for opposite
 * next steps. */
static volatile LONG g_iatPatched    = 0;

/* What actually arrived down the pipe, as counted at the far end.
 *
 * This is the only number that can tell "we sent nothing" apart from "we sent
 * it and the DLL never got it". Everything on the Python side can only report
 * that a write returned success, which is not the same claim -- a send that is
 * accepted by the pipe and then dropped, or garbled, looks identical from
 * there. If Python has sent commands and cmds is still 0, the channel is dead
 * whatever the write calls said.
 *
 * bad counts lines that matched no command. A line split across two reads and
 * reassembled wrongly lands here, so it is the corruption detector. */
static volatile LONG g_nCommands     = 0;
static volatile LONG g_nUnknown      = 0;

static volatile LONG g_nForeground   = 0;
static volatile LONG g_nActiveWindow = 0;
static volatile LONG g_nGetFocus     = 0;
static volatile LONG g_nKillFocus    = 0;
static volatile LONG g_nActivate     = 0;
static volatile LONG g_nActivateApp  = 0;

typedef HWND (WINAPI *fn_hwnd_void)(void);
static fn_hwnd_void real_GetForegroundWindow = NULL;
static fn_hwnd_void real_GetActiveWindow     = NULL;
static fn_hwnd_void real_GetFocus            = NULL;

static HWND WINAPI my_GetForegroundWindow(void) {
    InterlockedIncrement(&g_nForeground);
    if (g_spoofOn && g_spoofWnd) return g_spoofWnd;
    return real_GetForegroundWindow ? real_GetForegroundWindow() : NULL;
}

static HWND WINAPI my_GetActiveWindow(void) {
    InterlockedIncrement(&g_nActiveWindow);
    if (g_spoofOn && g_spoofWnd) return g_spoofWnd;
    return real_GetActiveWindow ? real_GetActiveWindow() : NULL;
}

static HWND WINAPI my_GetFocus(void) {
    InterlockedIncrement(&g_nGetFocus);
    if (g_spoofOn && g_spoofWnd) return g_spoofWnd;
    return real_GetFocus ? real_GetFocus() : NULL;
}

/* Patch one entry in the main executable's import table.
 *
 * The IAT is used rather than an inline/trampoline hook because it needs no
 * instruction decoding, no executable memory, and no third-party dependency.
 * The trade-off is that a call resolved through GetProcAddress at runtime
 * slips past it -- if the counters stay at zero, that is the first thing to
 * suspect.
 *
 * Only the main module is patched. The game's own logic lives in its exe; if
 * the counters say otherwise we widen this to every loaded module. */
static BOOL patch_iat(const char *dllName, const char *funcName,
                      void *replacement, void **original) {
    HMODULE base = GetModuleHandleW(NULL);
    if (!base) return FALSE;

    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return FALSE;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)((BYTE *)base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return FALSE;

    DWORD rva = nt->OptionalHeader
                  .DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress;
    if (!rva) return FALSE;

    IMAGE_IMPORT_DESCRIPTOR *desc = (IMAGE_IMPORT_DESCRIPTOR *)((BYTE *)base + rva);
    for (; desc->Name; desc++) {
        const char *name = (const char *)((BYTE *)base + desc->Name);
        if (_stricmp(name, dllName) != 0) continue;

        /* A bound import has no OriginalFirstThunk; the names then live in
         * FirstThunk itself, which is also what we overwrite. Read names from
         * whichever is present, write only to FirstThunk. */
        IMAGE_THUNK_DATA *nameThunk = desc->OriginalFirstThunk
            ? (IMAGE_THUNK_DATA *)((BYTE *)base + desc->OriginalFirstThunk)
            : (IMAGE_THUNK_DATA *)((BYTE *)base + desc->FirstThunk);
        IMAGE_THUNK_DATA *addrThunk =
            (IMAGE_THUNK_DATA *)((BYTE *)base + desc->FirstThunk);

        for (; nameThunk->u1.AddressOfData; nameThunk++, addrThunk++) {
            if (IMAGE_SNAP_BY_ORDINAL(nameThunk->u1.Ordinal)) continue;
            IMAGE_IMPORT_BY_NAME *imp = (IMAGE_IMPORT_BY_NAME *)
                ((BYTE *)base + nameThunk->u1.AddressOfData);
            if (strcmp((const char *)imp->Name, funcName) != 0) continue;

            DWORD old;
            if (!VirtualProtect(&addrThunk->u1.Function, sizeof(void *),
                                PAGE_READWRITE, &old)) {
                return FALSE;
            }
            if (original) *original = (void *)(ULONG_PTR)addrThunk->u1.Function;
            addrThunk->u1.Function = (ULONG_PTR)replacement;
            VirtualProtect(&addrThunk->u1.Function, sizeof(void *), old, &old);
            return TRUE;
        }
    }
    return FALSE;
}

static LRESULT CALLBACK my_WndProc(HWND h, UINT msg, WPARAM w, LPARAM l) {
    if (msg == WM_KILLFOCUS) {
        InterlockedIncrement(&g_nKillFocus);
        if (g_spoofOn) return 0;
    } else if (msg == WM_ACTIVATE && LOWORD(w) == WA_INACTIVE) {
        InterlockedIncrement(&g_nActivate);
        if (g_spoofOn) return 0;
    } else if (msg == WM_ACTIVATEAPP && w == FALSE) {
        InterlockedIncrement(&g_nActivateApp);
        if (g_spoofOn) return 0;
    }
    return CallWindowProcW(g_origWndProc, h, msg, w, l);
}

static void install_hooks(void) {
    static BOOL done = FALSE;
    if (done) return;
    done = TRUE;

    struct { const char *name; void *repl; void **orig; } wanted[] = {
        { "GetForegroundWindow", (void *)my_GetForegroundWindow,
          (void **)&real_GetForegroundWindow },
        { "GetActiveWindow",     (void *)my_GetActiveWindow,
          (void **)&real_GetActiveWindow },
        { "GetFocus",            (void *)my_GetFocus,
          (void **)&real_GetFocus },
    };
    int i;
    for (i = 0; i < 3; i++) {
        BOOL ok = patch_iat("user32.dll", wanted[i].name,
                            wanted[i].repl, wanted[i].orig);
        if (ok) InterlockedIncrement(&g_iatPatched);
        char msg[128];
        _snprintf_s(msg, sizeof(msg), _TRUNCATE, "IAT %s: %s",
                    wanted[i].name, ok ? "patched" : "NOT FOUND in main module");
        log_write(msg);
    }
}

static void subclass_window(HWND hwnd) {
    if (g_subclassed == hwnd) return;
    if (!IsWindow(hwnd)) { log_write("subclass: not a window"); return; }
    g_origWndProc = (WNDPROC)(ULONG_PTR)SetWindowLongPtrW(
        hwnd, GWLP_WNDPROC, (LONG_PTR)my_WndProc);
    if (g_origWndProc) {
        g_subclassed = hwnd;
        log_write("window proc subclassed");
    } else {
        log_write("SetWindowLongPtr failed; message hooks are not active");
    }
}

static void unsubclass_window(void) {
    if (!g_subclassed || !g_origWndProc) return;
    /* Only unhook if we are still the outermost proc. Restoring blindly
     * would tear out whatever subclassed us afterwards. */
    if (IsWindow(g_subclassed)) {
        WNDPROC current = (WNDPROC)(ULONG_PTR)GetWindowLongPtrW(
            g_subclassed, GWLP_WNDPROC);
        if (current == my_WndProc) {
            SetWindowLongPtrW(g_subclassed, GWLP_WNDPROC,
                              (LONG_PTR)g_origWndProc);
            log_write("window proc restored");
        } else {
            log_write("window proc NOT restored: someone subclassed after us");
        }
    }
    g_subclassed = NULL;
    g_origWndProc = NULL;
}

static void spoof_stats(void) {
    if (g_hPipe == INVALID_HANDLE_VALUE) return;
    char buf[256];
    int len = _snprintf_s(buf, sizeof(buf), _TRUNCATE,
        "STATS on=%ld iat=%ld sub=%d cmds=%ld bad=%ld "
        "fg=%ld active=%ld focus=%ld kill=%ld act=%ld actapp=%ld\n",
        (long)g_spoofOn, (long)g_iatPatched, g_subclassed ? 1 : 0,
        (long)g_nCommands, (long)g_nUnknown,
        (long)g_nForeground, (long)g_nActiveWindow, (long)g_nGetFocus,
        (long)g_nKillFocus, (long)g_nActivate, (long)g_nActivateApp);
    DWORD written;
    WriteFile(g_hPipe, buf, (DWORD)len, &written, NULL);
    log_write(buf);
}

static UINT parse_vk(const char *s) {
    if (strlen(s) == 1) {
        char c = s[0];
        if (c >= 'a' && c <= 'z') return c - 32;
        if (c >= 'A' && c <= 'Z') return c;
        if (c >= '0' && c <= '9') return c;
    }
    if (_stricmp(s, "enter") == 0 || _stricmp(s, "return") == 0) return VK_RETURN;
    if (_stricmp(s, "space") == 0) return VK_SPACE;
    if (_stricmp(s, "esc") == 0 || _stricmp(s, "escape") == 0) return VK_ESCAPE;
    if (_stricmp(s, "tab") == 0) return VK_TAB;
    if (_stricmp(s, "backspace") == 0 || _stricmp(s, "back") == 0) return VK_BACK;
    if (_stricmp(s, "delete") == 0 || _stricmp(s, "del") == 0) return VK_DELETE;
    if (_stricmp(s, "insert") == 0) return VK_INSERT;
    if (_stricmp(s, "home") == 0) return VK_HOME;
    if (_stricmp(s, "end") == 0) return VK_END;
    if (_stricmp(s, "left") == 0) return VK_LEFT;
    if (_stricmp(s, "right") == 0) return VK_RIGHT;
    if (_stricmp(s, "up") == 0) return VK_UP;
    if (_stricmp(s, "down") == 0) return VK_DOWN;
    if (_stricmp(s, "shift") == 0) return VK_SHIFT;
    if (_stricmp(s, "ctrl") == 0 || _stricmp(s, "control") == 0) return VK_CONTROL;
    if (_stricmp(s, "alt") == 0) return VK_MENU;
    if (s[0] == 'f' || s[0] == 'F') {
        int n = atoi(s + 1);
        if (n >= 1 && n <= 24) return VK_F1 + (n - 1);
    }
    return (UINT)atoi(s);
}

static DWORD parse_button(const char *s) {
    if (_stricmp(s, "left") == 0) return 1;
    if (_stricmp(s, "right") == 0) return 2;
    if (_stricmp(s, "middle") == 0) return 4;
    return (DWORD)atoi(s);
}

static void do_key_down(UINT vk) {
    keybd_event((BYTE)vk, 0, 0, 0);
}

static void do_key_up(UINT vk) {
    keybd_event((BYTE)vk, 0, KEYEVENTF_KEYUP, 0);
}

static void do_mouse_down(int x, int y, DWORD btn) {
    SetCursorPos(x, y);
    DWORD flags = 0;
    if (btn & 1) flags |= MOUSEEVENTF_LEFTDOWN;
    if (btn & 2) flags |= MOUSEEVENTF_RIGHTDOWN;
    if (btn & 4) flags |= MOUSEEVENTF_MIDDLEDOWN;
    if (flags) mouse_event(flags, x, y, 0, 0);
}

static void do_mouse_up(int x, int y, DWORD btn) {
    SetCursorPos(x, y);
    DWORD flags = 0;
    if (btn & 1) flags |= MOUSEEVENTF_LEFTUP;
    if (btn & 2) flags |= MOUSEEVENTF_RIGHTUP;
    if (btn & 4) flags |= MOUSEEVENTF_MIDDLEUP;
    if (flags) mouse_event(flags, x, y, 0, 0);
}

static void process_cmd(char *line) {
    char *arg = strchr(line, ':');
    if (arg) { *arg = '\0'; arg++; }
    if (_stricmp(line, "KEY_DOWN") == 0 && arg) {
        do_key_down(parse_vk(arg));
    } else if (_stricmp(line, "KEY_UP") == 0 && arg) {
        do_key_up(parse_vk(arg));
    } else if (_stricmp(line, "MOUSE_DOWN") == 0 && arg) {
        int x = 0, y = 0; char b[32] = "1";
        sscanf(arg, "%d,%d,%31s", &x, &y, b);
        do_mouse_down(x, y, parse_button(b));
    } else if (_stricmp(line, "MOUSE_UP") == 0 && arg) {
        int x = 0, y = 0; char b[32] = "1";
        sscanf(arg, "%d,%d,%31s", &x, &y, b);
        do_mouse_up(x, y, parse_button(b));
    } else if (_stricmp(line, "SPOOF_WATCH") == 0 && arg) {
        /* Observe only: install the subclass, leave spoofing off.
         *
         * Without this, stage 1 is a measurement that can only ever return one
         * answer. The subclass used to go in only on SPOOF_ON, and stage 1
         * never sends SPOOF_ON -- so kill/act/actapp were structurally pinned
         * at zero, and "the game is told by window messages" could not come
         * back even when it was the truth. The verdict could only land on
         * polls or no-hooks-hit.
         *
         * Installing the subclass changes no behaviour: with spoofing off
         * my_WndProc only counts and forwards every message untouched. */
        HWND hwnd = (HWND)(ULONG_PTR)_strtoui64(arg, NULL, 0);
        if (!hwnd || !IsWindow(hwnd)) {
            log_write("SPOOF_WATCH rejected: bad hwnd");
        } else {
            g_spoofWnd = hwnd;
            subclass_window(hwnd);
            log_write("SPOOF_WATCH: counting only, nothing is spoofed");
        }
    } else if (_stricmp(line, "SPOOF_ON") == 0 && arg) {
        /* The injector passes the hwnd -- it already knows which window it
         * targeted, and guessing from inside the process would be worse. */
        HWND hwnd = (HWND)(ULONG_PTR)_strtoui64(arg, NULL, 0);
        if (!hwnd || !IsWindow(hwnd)) {
            log_write("SPOOF_ON rejected: bad hwnd");
        } else {
            g_spoofWnd = hwnd;
            subclass_window(hwnd);
            InterlockedExchange(&g_spoofOn, 1);
            log_write("SPOOF_ON: the game is now told it is focused");
        }
    } else if (_stricmp(line, "SPOOF_OFF") == 0) {
        InterlockedExchange(&g_spoofOn, 0);
        unsubclass_window();
        g_spoofWnd = NULL;
        log_write("SPOOF_OFF: back to the truth");
    } else if (_stricmp(line, "SPOOF_STATS") == 0) {
        spoof_stats();
    } else if (_stricmp(line, "PING") == 0) {
        const char *resp = "PONG\n";
        DWORD written;
        WriteFile(g_hPipe, resp, (DWORD)strlen(resp), &written, NULL);
    } else {
        /* Matched nothing. Either the two sides disagree on a command name, or
         * the line arrived damaged. Silently ignoring it is how a broken pipe
         * looks exactly like an idle one. */
        char msg[192];
        _snprintf_s(msg, sizeof(msg), _TRUNCATE, "unknown command: %.120s", line);
        log_write(msg);
        InterlockedIncrement(&g_nUnknown);
        return;
    }
    /* Only the unknown branch above returns early, so getting here means some
     * branch accepted the line. */
    InterlockedIncrement(&g_nCommands);
}

static DWORD WINAPI pipe_thread(LPVOID param) {
    (void)param;
    char buf[BUF_SIZE];
    DWORD read;
    log_write("pipe_thread started");

    /* Install the IAT patches here rather than in DllMain: doing less under
     * the loader lock is always right, and nothing needs them earlier.
     *
     * They are installed even though spoofing starts OFF. With g_spoofOn at 0
     * every replacement just counts the call and forwards to the real
     * function, so behaviour is unchanged -- but SPOOF_STATS can then tell us
     * which focus API the game actually uses, which is the one thing the
     * external probe could never see. */
    install_hooks();

    while (g_running) {
        log_write("Trying to connect to pipe...");
        g_hPipe = CreateFileW(PIPE_NAME, GENERIC_READ | GENERIC_WRITE, 0, NULL,
                               OPEN_EXISTING, 0, NULL);
        if (g_hPipe == INVALID_HANDLE_VALUE) {
            char errmsg[128];
            _snprintf_s(errmsg, sizeof(errmsg), _TRUNCATE,
                "CreateFileW failed, error=%u", (unsigned)GetLastError());
            log_write(errmsg);
            Sleep(200);
            continue;
        }
        log_write("Connected to pipe successfully");

        const char *hello = "HELLO\n";
        DWORD written;
        WriteFile(g_hPipe, hello, (DWORD)strlen(hello), &written, NULL);
        log_write("Sent HELLO to server");

        /* `used` is how much of buf holds a line that has not been
         * terminated yet. The old loop moved that remainder to the front and
         * then read the next chunk over the top of it at buf[0], so a command
         * split across two reads was silently corrupted rather than
         * reassembled -- the memmove could never do anything. */
        DWORD used = 0;
        memset(buf, 0, sizeof(buf));
        while (g_running &&
               ReadFile(g_hPipe, buf + used, (DWORD)(sizeof(buf) - 1 - used),
                        &read, NULL) && read > 0) {
            used += read;
            buf[used] = '\0';

            char *start = buf;
            char *nl;
            while ((nl = strchr(start, '\n')) != NULL) {
                *nl = '\0';
                if (*start) process_cmd(start);
                start = nl + 1;
            }

            used = (DWORD)strlen(start);
            memmove(buf, start, used + 1);

            if (used >= sizeof(buf) - 1) {
                /* A full buffer with no newline in it. Keeping it would leave
                 * the next ReadFile asking for zero bytes forever; dropping it
                 * loses one malformed command instead of wedging the thread. */
                log_write("no newline in a full buffer; dropping it");
                used = 0;
                buf[0] = '\0';
            }
        }

        CloseHandle(g_hPipe);
        g_hPipe = INVALID_HANDLE_VALUE;
        Sleep(200);
    }
    return 0;
}

BOOL APIENTRY DllMain(HMODULE hModule, DWORD reason, LPVOID reserved) {
    (void)reserved;
    switch (reason) {
    case DLL_PROCESS_ATTACH:
        log_init();
        DisableThreadLibraryCalls(hModule);
        g_running = TRUE;
        g_hThread = CreateThread(NULL, 0, pipe_thread, NULL, 0, NULL);
        if (g_hThread == NULL) {
            log_write("CreateThread failed!");
        } else {
            CloseHandle(g_hThread);
        }
        break;
    case DLL_PROCESS_DETACH:
        log_write("DLL unload");
        /* Stop lying before we go. Leaving a subclassed window proc pointing
         * into an unloaded DLL would crash the game on the next message. */
        InterlockedExchange(&g_spoofOn, 0);
        unsubclass_window();
        g_running = FALSE;
        if (g_hPipe != INVALID_HANDLE_VALUE) {
            CloseHandle(g_hPipe);
            g_hPipe = INVALID_HANDLE_VALUE;
        }
        if (g_hLog != INVALID_HANDLE_VALUE) {
            CloseHandle(g_hLog);
            g_hLog = INVALID_HANDLE_VALUE;
        }
        break;
    }
    return TRUE;
}
