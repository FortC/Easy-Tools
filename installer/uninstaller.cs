using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Management;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Win32;

// Compiled as a Windows GUI executable. No shell, CMD or PowerShell is started.
internal static class Uninstaller
{
    private const string Manifest = ".easy-tools-uninstall.txt";
    private const string Signature = "Easy-Tools uninstall manifest v1";
    private const string RegistryPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\EasyTools";

    private static string Full(string path)
    {
        string full = Path.GetFullPath(path);
        return full.Length > Path.GetPathRoot(full).Length ? full.TrimEnd('\\', '/') : full;
    }

    private static string ValidateRoot(string path)
    {
        string root = Full(path);
        if (root.Length == 0 || root.Equals(Full(Path.GetPathRoot(root)), StringComparison.OrdinalIgnoreCase))
            throw new IOException("不能把磁盘根目录作为卸载目录。");
        foreach (Environment.SpecialFolder special in new[] {
            Environment.SpecialFolder.Windows, Environment.SpecialFolder.System,
            Environment.SpecialFolder.UserProfile, Environment.SpecialFolder.DesktopDirectory,
            Environment.SpecialFolder.ProgramFiles, Environment.SpecialFolder.ProgramFilesX86,
            Environment.SpecialFolder.ApplicationData, Environment.SpecialFolder.LocalApplicationData })
        {
            string value = Environment.GetFolderPath(special);
            if (value.Length != 0 && root.Equals(Full(value), StringComparison.OrdinalIgnoreCase))
                throw new IOException("不能把系统或用户文件夹作为卸载目录。");
        }
        for (DirectoryInfo dir = new DirectoryInfo(root); dir != null; dir = dir.Parent)
            if (dir.Exists && (dir.Attributes & FileAttributes.ReparsePoint) != 0)
                throw new IOException("安装目录包含目录链接，请先检查真实安装位置。");
        return root;
    }

    private static string OwnedPath(string root, string relative)
    {
        if (String.IsNullOrWhiteSpace(relative) || Path.IsPathRooted(relative) || relative.IndexOf(':') >= 0)
            throw new IOException("安装清单路径不正确。");
        foreach (string part in relative.Split(new[] { '\\', '/' }))
            if (part == ".." || part == ".") throw new IOException("安装清单路径越界。");
        string result = Full(Path.Combine(root, relative));
        if (!result.StartsWith(root + "\\", StringComparison.OrdinalIgnoreCase))
            throw new IOException("安装清单路径越界。");
        for (DirectoryInfo dir = new FileInfo(result).Directory; dir != null; dir = dir.Parent)
        {
            if (dir.Exists && (dir.Attributes & FileAttributes.ReparsePoint) != 0)
                throw new IOException("安装文件包含目录链接，已停止清理。");
            if (dir.FullName.Equals(root, StringComparison.OrdinalIgnoreCase)) break;
        }
        if (File.Exists(result) && (File.GetAttributes(result) & FileAttributes.ReparsePoint) != 0)
            throw new IOException("安装文件包含文件链接，已停止清理。");
        return result;
    }

    private static List<string> ReadManifest(string root)
    {
        string[] lines = File.ReadAllLines(Path.Combine(root, Manifest), Encoding.UTF8);
        if (lines.Length < 3 || lines[0] != Signature || !Full(lines[1]).Equals(root, StringComparison.OrdinalIgnoreCase))
            throw new IOException("安装清单无效，已停止卸载。");
        var paths = new List<string>();
        var unique = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        for (int i = 2; i < lines.Length; i++)
        {
            string path = OwnedPath(root, lines[i]);
            if (unique.Add(path)) paths.Add(path);
        }
        if (!unique.Contains(Path.Combine(root, "Easy-Tools.exe")) || !unique.Contains(Path.Combine(root, "Uninstall.exe")))
            throw new IOException("安装清单缺少程序入口，已停止卸载。");
        return paths;
    }

    private static void StopOwnedProcesses(string root)
    {
        StopLegacyLoop();
        foreach (string name in new[] { "Easy-Tools", "llama-server" })
        foreach (Process process in Process.GetProcessesByName(name))
        using (process)
        {
            try
            {
                string path = Full(process.MainModule.FileName);
                bool owned = path.Equals(Path.Combine(root, "Easy-Tools.exe"), StringComparison.OrdinalIgnoreCase)
                    || (name == "llama-server" && path.StartsWith(root + "\\_internal\\", StringComparison.OrdinalIgnoreCase));
                if (owned) { process.Kill(); process.WaitForExit(2000); }
            }
            catch (InvalidOperationException) { }
            catch (System.ComponentModel.Win32Exception) { }
        }
    }

    private static void StopLegacyLoop()
    {
        string script = Path.Combine(Path.GetTempPath(), "et_uninstall_run.cmd");
        if (!File.Exists(script)) return;
        string content;
        try { content = File.ReadAllText(script, Encoding.UTF8); } catch (IOException) { return; }
        if (!content.Contains("copy \"%~f0\"") || !content.Contains("start \"\" /min cmd /c")
            || !content.Contains("et_uninstall_run.cmd")) return;
        // Delete only this known self-copying legacy script, then stop only
        // processes whose command line names that exact per-user temp path.
        for (int attempt = 0; attempt < 3; attempt++)
        {
            try { File.Delete(script); } catch (IOException) { } catch (UnauthorizedAccessException) { }
            try
            {
                using (var query = new ManagementObjectSearcher("SELECT ProcessId,CommandLine FROM Win32_Process WHERE Name='cmd.exe' OR Name='powershell.exe' OR Name='pwsh.exe'"))
                {
                query.Options.Timeout = TimeSpan.FromSeconds(3);
                using (ManagementObjectCollection matches = query.Get())
                foreach (ManagementObject item in matches)
                using (item)
                {
                    string command = Convert.ToString(item["CommandLine"]);
                    if (command.IndexOf(script, StringComparison.OrdinalIgnoreCase) < 0) continue;
                    try
                    {
                        using (Process process = Process.GetProcessById(Convert.ToInt32(item["ProcessId"])))
                        { process.Kill(); }
                    }
                    catch (ArgumentException) { }
                    catch (InvalidOperationException) { }
                    catch (System.ComponentModel.Win32Exception) { }
                }
                }
            }
            catch (ManagementException) { }
            catch (UnauthorizedAccessException) { }
            if (!File.Exists(script)) break;
        }
    }

    private static void RemoveOwnedShortcut(string path, string root)
    {
        if (!File.Exists(path)) return;
        object shell = null, shortcut = null;
        try
        {
            Type type = Type.GetTypeFromProgID("WScript.Shell");
            shell = Activator.CreateInstance(type);
            shortcut = type.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { path });
            string destination = Convert.ToString(shortcut.GetType().InvokeMember("TargetPath", BindingFlags.GetProperty, null, shortcut, null));
            if (!String.IsNullOrEmpty(destination) && Full(destination).Equals(Path.Combine(root, "Easy-Tools.exe"), StringComparison.OrdinalIgnoreCase))
                File.Delete(path);
        }
        finally
        {
            if (shortcut != null && Marshal.IsComObject(shortcut)) Marshal.FinalReleaseComObject(shortcut);
            if (shell != null && Marshal.IsComObject(shell)) Marshal.FinalReleaseComObject(shell);
        }
    }

    private static void PruneEmptyParents(string file, string root)
    {
        DirectoryInfo directory = new FileInfo(file).Directory;
        while (directory != null && directory.FullName.StartsWith(root + "\\", StringComparison.OrdinalIgnoreCase))
        {
            string parent = directory.FullName;
            try { Directory.Delete(parent, false); }
            catch (IOException) { break; }
            catch (UnauthorizedAccessException) { break; }
            directory = directory.Parent;
        }
    }

    private static void Clean(string root, Action<int, string> progress)
    {
        List<string> files = ReadManifest(root);
        files.RemoveAll(delegate(string path) { return path.Equals(Path.Combine(root, "Uninstall.exe"), StringComparison.OrdinalIgnoreCase); });
        progress(0, "正在关闭此目录中的 Easy-Tools…");
        StopOwnedProcesses(root);
        for (int attempt = 0; attempt < 8; attempt++)
        {
            int remaining = 0;
            for (int i = 0; i < files.Count; i++)
            {
                string file = files[i];
                try
                {
                    // Recheck ancestors immediately before deleting each file.
                    OwnedPath(root, file.Substring(root.Length + 1));
                    if (File.Exists(file)) { File.SetAttributes(file, FileAttributes.Normal); File.Delete(file); }
                    PruneEmptyParents(file, root);
                }
                catch (IOException) { remaining++; }
                catch (UnauthorizedAccessException) { remaining++; }
                if ((i % 32) == 0) progress((i + 1) * 90 / Math.Max(1, files.Count), "正在移除程序文件…");
            }
            if (remaining == 0) break;
            if (attempt == 7) throw new IOException("部分文件仍被占用。请关闭相关窗口后重新卸载；用户配置未删除。");
            Thread.Sleep(400);
        }
        progress(95, "正在清理快捷方式和卸载记录…");
        RemoveOwnedShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Easy-Tools.lnk"), root);
        RemoveOwnedShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Easy-Tools.lnk"), root);
        string installedHelper = Path.Combine(root, "Uninstall.exe");
        for (int attempt = 0; attempt < 8 && File.Exists(installedHelper); attempt++)
        {
            try { File.Delete(installedHelper); }
            catch (IOException) { if (attempt == 7) throw; Thread.Sleep(400); }
        }
        using (RegistryKey user = RegistryKey.OpenBaseKey(RegistryHive.CurrentUser, Environment.Is64BitOperatingSystem ? RegistryView.Registry64 : RegistryView.Registry32))
        {
            bool owned;
            using (RegistryKey key = user.OpenSubKey(RegistryPath))
                owned = key != null && !String.IsNullOrEmpty(Convert.ToString(key.GetValue("InstallLocation")))
                    && Full(Convert.ToString(key.GetValue("InstallLocation"))).Equals(root, StringComparison.OrdinalIgnoreCase);
            if (owned) user.DeleteSubKeyTree(RegistryPath, false);
        }
        File.Delete(Path.Combine(root, Manifest));
        try { Directory.Delete(root, false); } catch (IOException) { } catch (UnauthorizedAccessException) { }
        progress(100, "卸载完成，用户配置及非程序文件已保留。");
    }

    [STAThread]
    private static int Main(string[] args)
    {
        bool quiet = Array.IndexOf(args, "--quiet") >= 0;
        bool stopOnly = args.Length == 2 && args[0] == "--stop-app";
        try
        {
            if (stopOnly) { StopOwnedProcesses(ValidateRoot(args[1])); return 0; }
            bool worker = args.Length >= 3 && args[0] == "--worker";
            if (!worker)
            {
                string root = ValidateRoot(Application.StartupPath);
                ReadManifest(root);
                // Exactly one handoff to a uniquely named temporary GUI helper.
                string temporary = Path.Combine(Path.GetTempPath(), "Easy-Tools-Uninstall-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(temporary);
                string helper = Path.Combine(temporary, "Uninstall.exe");
                File.Copy(Application.ExecutablePath, helper);
                Process.Start(new ProcessStartInfo(helper, "--worker \"" + root + "\" " + Process.GetCurrentProcess().Id + (quiet ? " --quiet" : "")) {
                    UseShellExecute = false, CreateNoWindow = true, WindowStyle = quiet ? ProcessWindowStyle.Hidden : ProcessWindowStyle.Normal, WorkingDirectory = temporary });
                return 0;
            }
            string target = ValidateRoot(args[1]);
            ReadManifest(target);
            int parentId;
            if (Int32.TryParse(args[2], out parentId))
            {
                try { using (Process parent = Process.GetProcessById(parentId)) parent.WaitForExit(5000); }
                catch (ArgumentException) { }
            }
            string identity;
            using (var hash = System.Security.Cryptography.SHA256.Create())
                identity = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(target.ToUpperInvariant()))).Replace("-", "").Substring(0, 32);
            using (var mutex = new Mutex(false, "Local\\EasyToolsUninstall-" + identity))
            {
                bool locked;
                try { locked = mutex.WaitOne(0); } catch (AbandonedMutexException) { locked = true; }
                if (!locked) return 0;
                try
                {
                    if (quiet) { Clean(target, delegate(int n, string s) { }); return 0; }
                    Application.EnableVisualStyles();
                    var window = new Form { Text = "卸载 Easy-Tools", ClientSize = new Size(440, 130), StartPosition = FormStartPosition.CenterScreen,
                        FormBorderStyle = FormBorderStyle.FixedDialog, MaximizeBox = false, MinimizeBox = false, ControlBox = false };
                    var label = new Label { Text = "正在准备卸载…", Left = 22, Top = 22, Width = 395, Height = 42 };
                    var bar = new ProgressBar { Left = 22, Top = 80, Width = 395, Height = 20 };
                    window.Controls.Add(label); window.Controls.Add(bar);
                    Exception failure = null;
                    window.Shown += delegate {
                        Task.Factory.StartNew(delegate {
                            try { Clean(target, delegate(int n, string s) { window.BeginInvoke((Action)delegate { label.Text = s; bar.Value = Math.Min(100, n); }); }); }
                            catch (Exception error) { failure = error; }
                            window.BeginInvoke((Action)delegate { window.Close(); });
                        });
                    };
                    Application.Run(window);
                    if (failure != null) throw failure;
                    return 0;
                }
                finally { mutex.ReleaseMutex(); }
            }
        }
        catch (Exception error)
        {
            if (!quiet && !stopOnly) MessageBox.Show(error.Message, "Easy-Tools 卸载未完成", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 1;
        }
    }
}
