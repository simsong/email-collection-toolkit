// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Exercise WinForms menu commands through their native MSAA accessibility provider.
// Python's .NET Framework MenuStrip is not exposed by every UI Automation client.
// Limit discovery to child-window menu bars and the explicit File/Quit action.
// Invoke the real accessible menu action, preserving the application's quit path.
// Installation acceptance still requires the launched process to exit afterwards.
// This helper neither sends a forced close nor terminates an application process.
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using Accessibility;

public static class ECTNativeMenu {
    private delegate void EventCallback(IntPtr hook, uint kind, IntPtr window, int objectId,
        int childId, uint thread, uint time);
    [StructLayout(LayoutKind.Sequential)]
    private struct Message {
        public IntPtr window;
        public uint kind;
        public UIntPtr wparam;
        public IntPtr lparam;
        public uint time;
        public int x, y;
        public uint privateData;
    }
    [DllImport("user32.dll", SetLastError=true)]
    private static extern IntPtr SetWinEventHook(uint first, uint last, IntPtr module,
        EventCallback callback, uint process, uint thread, uint flags);
    [DllImport("user32.dll")]
    private static extern bool UnhookWinEvent(IntPtr hook);
    [DllImport("user32.dll", SetLastError=true)]
    private static extern uint MsgWaitForMultipleObjectsEx(uint count, IntPtr[] handles,
        uint milliseconds, uint mask, uint flags);
    [DllImport("user32.dll")]
    private static extern bool PeekMessage(out Message message, IntPtr window, uint first, uint last, uint remove);
    [DllImport("user32.dll")]
    private static extern bool TranslateMessage(ref Message message);
    [DllImport("user32.dll")]
    private static extern IntPtr DispatchMessage(ref Message message);

    private static void Until(Process process, Func<bool> ready, int timeoutMilliseconds) {
        // Subscribe before the first predicate read, and pump actual out-of-context WinEvents.
        EventCallback changed = delegate { };
        IntPtr hook = SetWinEventHook(1, 0x7FFFFFFF, IntPtr.Zero, changed, (uint)process.Id, 0, 0);
        if (hook == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        Stopwatch deadline = Stopwatch.StartNew();
        try {
            while (!ready()) {
                if (process.HasExited) throw new InvalidOperationException("GUI exited before native readiness");
                double remaining = timeoutMilliseconds - deadline.Elapsed.TotalMilliseconds;
                if (remaining <= 0) throw new TimeoutException("Native GUI readiness deadline expired");
                uint result = MsgWaitForMultipleObjectsEx(1, new IntPtr[] { process.Handle },
                    (uint)Math.Ceiling(remaining), 0x04FF, 4);
                if (result == 0xFFFFFFFF) throw new Win32Exception(Marshal.GetLastWin32Error());
                if (result == 0x102) throw new TimeoutException("Native GUI readiness deadline expired");
                Message message;
                while (PeekMessage(out message, IntPtr.Zero, 0, 0, 1)) {
                    TranslateMessage(ref message);
                    DispatchMessage(ref message);
                }
            }
        } finally {
            UnhookWinEvent(hook);
            GC.KeepAlive(changed);
        }
    }

    public static IntPtr WaitForWindow(int pid, int timeoutMilliseconds) {
        using (Process process = Process.GetProcessById(pid)) {
            Until(process, delegate { process.Refresh(); return process.MainWindowHandle != IntPtr.Zero; }, timeoutMilliseconds);
            return process.MainWindowHandle;
        }
    }

    public static void WaitAndInvokeQuit(int pid, IntPtr window, int timeoutMilliseconds) {
        bool opened = false;
        using (Process process = Process.GetProcessById(pid)) {
            Until(process, delegate { return TryQuit(window, ref opened); }, timeoutMilliseconds);
        }
    }
    private delegate bool WindowCallback(IntPtr window, IntPtr parameter);
    [DllImport("user32.dll")]
    private static extern bool EnumChildWindows(IntPtr parent, WindowCallback callback, IntPtr parameter);
    [DllImport("oleacc.dll")]
    private static extern int AccessibleObjectFromWindow(IntPtr window, uint objectId,
        ref Guid interfaceId, [MarshalAs(UnmanagedType.Interface)] out object accessible);
    [DllImport("oleacc.dll")]
    private static extern int AccessibleChildren(IAccessible parent, int start, int count,
        [Out, MarshalAs(UnmanagedType.LPArray, SizeParamIndex = 2)] object[] children, out int obtained);

    private static IAccessible Child(IAccessible parent, string name) {
        int count = parent.accChildCount;
        if (count < 0 || count > 128) throw new InvalidOperationException("Unexpected native menu size");
        object[] children = new object[count];
        int obtained;
        int result = AccessibleChildren(parent, 0, count, children, out obtained);
        if (result < 0) Marshal.ThrowExceptionForHR(result);
        for (int index = 0; index < obtained; index++) {
            IAccessible child = children[index] as IAccessible;
            if (child != null && child.get_accName(0) == name) return child;
        }
        return null;
    }

    public static bool InvokeQuit(IntPtr window) {
        bool opened = false;
        return TryQuit(window, ref opened);
    }

    private static bool TryQuit(IntPtr window, ref bool opened) {
        IAccessible file = null;
        EnumChildWindows(window, delegate(IntPtr child, IntPtr unused) {
            Guid interfaceId = new Guid("618736E0-3C3D-11CF-810C-00AA00389B71");
            object accessible;
            if (AccessibleObjectFromWindow(child, 0xFFFFFFFC, ref interfaceId, out accessible) != 0) return true;
            IAccessible menu = accessible as IAccessible;
            // ROLE_SYSTEM_MENUBAR excludes message HTML and unrelated controls.
            if (menu != null && Convert.ToInt32(menu.get_accRole(0)) == 2) file = Child(menu, "File");
            return file == null;
        }, IntPtr.Zero);
        if (file == null) return false;
        if (!opened) {
            file.accDoDefaultAction(0);
            opened = true;
        }
        IAccessible quit = Child(file, "Quit");
        if (quit == null) return false;
        quit.accDoDefaultAction(0);
        return true;
    }
}
