// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Exercise WinForms menu commands through their native MSAA accessibility provider.
// Python's .NET Framework MenuStrip is not exposed by every UI Automation client.
// Limit discovery to child-window menu bars and the explicit File/Quit action.
// Invoke the real accessible menu action, preserving the application's quit path.
// Installation acceptance still requires the launched process to exit afterwards.
// This helper neither sends a forced close nor terminates an application process.
using System;
using System.Runtime.InteropServices;
using Accessibility;

public static class ECTNativeMenu {
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
        file.accDoDefaultAction(0);
        IAccessible quit = Child(file, "Quit");
        if (quit == null) return false;
        quit.accDoDefaultAction(0);
        return true;
    }
}
