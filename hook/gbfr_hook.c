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
    } else if (_stricmp(line, "PING") == 0) {
        const char *resp = "PONG\n";
        DWORD written;
        WriteFile(g_hPipe, resp, (DWORD)strlen(resp), &written, NULL);
    }
}

static DWORD WINAPI pipe_thread(LPVOID param) {
    (void)param;
    char buf[BUF_SIZE];
    DWORD read;
    log_write("pipe_thread started");

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

        memset(buf, 0, sizeof(buf));
        while (g_running && ReadFile(g_hPipe, buf, sizeof(buf) - 1, &read, NULL)) {
            buf[read] = '\0';
            char *start = buf;
            char *nl;
            while ((nl = strchr(start, '\n')) != NULL) {
                *nl = '\0';
                if (*start) process_cmd(start);
                start = nl + 1;
            }
            if (start != buf) {
                memmove(buf, start, strlen(start) + 1);
            }
            memset(buf + strlen(buf), 0, sizeof(buf) - strlen(buf));
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
