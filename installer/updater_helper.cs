using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;

internal static class EasyToolsUpdater
{
    private const string ManifestName = ".easy-tools-uninstall.txt";
    private const string ManifestHeader = "Easy-Tools uninstall manifest v1";
    private const string AppName = "Easy-Tools";
    private static JavaScriptSerializer Json()
    {
        return new JavaScriptSerializer { MaxJsonLength = Int32.MaxValue, RecursionLimit = 256 };
    }

    private static string Safe(string root, string relative)
    {
        if (String.IsNullOrWhiteSpace(relative) || relative.StartsWith("/") || relative.StartsWith("\\") ||
            Path.IsPathRooted(relative) || relative.IndexOf(':') >= 0 || relative.IndexOf('\0') >= 0)
            throw new InvalidDataException("更新包包含不安全的相对路径。");
        string[] parts = relative.Split(new[] { '/', '\\' }, StringSplitOptions.None);
        if (parts.Any(p => String.IsNullOrWhiteSpace(p) || p == "." || p == ".." || p.EndsWith(".") || p.EndsWith(" ")))
            throw new InvalidDataException("更新包包含不安全的相对路径。");
        foreach (string part in parts)
        {
            string stem = part.Split('.')[0].ToUpperInvariant();
            if (new[] { "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9" }.Contains(stem))
                throw new InvalidDataException("更新包包含 Windows 保留路径。");
        }
        string portableRelative = String.Join(Path.DirectorySeparatorChar.ToString(), parts);
        string full = Path.GetFullPath(Path.Combine(root, portableRelative));
        string prefix = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (!full.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("更新路径超出安装目录。");
        string cursor = full;
        while (cursor.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
        {
            if (File.Exists(cursor) || Directory.Exists(cursor))
                if ((File.GetAttributes(cursor) & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidDataException("更新路径包含目录链接。");
            cursor = Path.GetDirectoryName(cursor);
            if (cursor == null) break;
        }
        return full;
    }

    private static List<string> ReadInstalled(string install)
    {
        string path = Path.Combine(install, ManifestName);
        if (!File.Exists(path))
            throw new InvalidDataException("没有找到安装清单，请使用正式安装器恢复后再更新。");
        string[] lines = File.ReadAllLines(path, Encoding.UTF8);
        if (lines.Length < 3 || lines[0].TrimStart('\uFEFF') != ManifestHeader ||
            !String.Equals(Path.GetFullPath(lines[1]), Path.GetFullPath(install), StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("安装清单与当前安装目录不匹配。");
        return lines.Skip(2).Where(x => !String.IsNullOrWhiteSpace(x))
                     .Select(x => { Safe(install, x); return x; }).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
    }

    private static string HashFile(string path)
    {
        using (SHA256 sha = SHA256.Create())
        using (FileStream stream = File.OpenRead(path))
            return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
    }

    private static string Normalize(string path)
    {
        return path.Replace('\\', '/');
    }

    private static void WaitForParent(int pid)
    {
        if (pid < 1) throw new ArgumentException("应用进程编号无效。");
        Process parent;
        try { parent = Process.GetProcessById(pid); }
        catch (ArgumentException) { return; }
        using (parent)
            if (!parent.WaitForExit(120000))
                throw new TimeoutException("Easy-Tools 尚未退出。请退出程序后重试更新。");
    }

    private static Dictionary<string, object> ReadRelease(string stage)
    {
        string path = Path.Combine(stage, ".update-manifest.json");
        if (!File.Exists(path)) throw new InvalidDataException("更新包清单不存在。");
        return Json().Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8));
    }

    private static List<Dictionary<string, object>> NewFiles(Dictionary<string, object> release, string stage)
    {
        object raw;
        if (!release.TryGetValue("files", out raw) || !(raw is System.Collections.ArrayList))
            throw new InvalidDataException("更新包文件列表无效。");
        var result = new List<Dictionary<string, object>>();
        foreach (object item in (System.Collections.ArrayList)raw)
        {
            var file = item as Dictionary<string, object>;
            if (file == null) throw new InvalidDataException("更新文件记录无效。");
            string relative = Convert.ToString(file["path"]);
            Safe(stage, relative);
            Safe(stage, relative);
            string hash = Convert.ToString(file["sha256"]);
            if (hash.Length != 64 || !hash.All(c => Uri.IsHexDigit(c)))
                throw new InvalidDataException("更新文件 SHA-256 格式错误。");
            string source = Path.Combine(stage, relative.Replace('/', Path.DirectorySeparatorChar));
            if (!File.Exists(source)) throw new FileNotFoundException("更新文件缺失。", relative);
            if (!String.Equals(HashFile(source), hash, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("下载文件校验失败：" + relative);
            result.Add(file);
        }
        if (result.Count == 0) throw new InvalidDataException("更新包没有任何文件。");
        if (result.Any(x => String.Equals(Convert.ToString(x["path"]), ManifestName, StringComparison.OrdinalIgnoreCase) ||
                            String.Equals(Convert.ToString(x["path"]), "Uninstall.exe", StringComparison.OrdinalIgnoreCase)))
            throw new InvalidDataException("更新包试图覆盖安装维护文件。");
        return result;
    }

    private static void Restore(string install, string backup, IEnumerable<string> paths, string oldManifest)
    {
        foreach (string relative in paths.Reverse())
        {
            string dst = Safe(install, relative), saved = Safe(backup, relative);
            if (File.Exists(saved))
            {
                Directory.CreateDirectory(Path.GetDirectoryName(dst));
                File.Copy(saved, dst, true);
            }
            else if (File.Exists(dst)) File.Delete(dst);
        }
        if (File.Exists(oldManifest)) File.Copy(oldManifest, Path.Combine(install, ManifestName), true);
    }

    private static int Apply(int pid, string install, string stage, string data)
    {
        install = Path.GetFullPath(install); stage = Path.GetFullPath(stage); data = Path.GetFullPath(data);
        if (!Directory.Exists(install) || !Directory.Exists(stage)) throw new DirectoryNotFoundException("安装目录或暂存目录不存在。");
        string stagePrefix = Path.GetFullPath(Path.Combine(data, "updates", "staged")) + Path.DirectorySeparatorChar;
        if (!stage.StartsWith(stagePrefix, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("暂存目录不在 Easy-Tools 数据目录中。");
        WaitForParent(pid);

        var release = ReadRelease(stage);
        if (!String.Equals(Convert.ToString(release["app"]), "easy-tools", StringComparison.Ordinal) ||
            !String.Equals(Convert.ToString(release["platform"]), "windows-x64", StringComparison.Ordinal))
            throw new InvalidDataException("此更新包不适用于当前应用或平台。");
        string version = Convert.ToString(release["version"]);
        if (String.IsNullOrWhiteSpace(version) || version.Any(c => !(Char.IsLetterOrDigit(c) || c == '.' || c == '-' || c == '_')))
            throw new InvalidDataException("更新版本号无效。");
        var newFiles = NewFiles(release, stage);
        var oldFiles = ReadInstalled(install).Select(Normalize).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
        var newNames = new HashSet<string>(newFiles.Select(f => Normalize(Convert.ToString(f["path"]))), StringComparer.OrdinalIgnoreCase);
        var touched = oldFiles.Where(x => !String.Equals(x, "Uninstall.exe", StringComparison.OrdinalIgnoreCase)).ToList();
        foreach (var file in newFiles)
            if (!touched.Contains(Normalize(Convert.ToString(file["path"])), StringComparer.OrdinalIgnoreCase))
                touched.Add(Normalize(Convert.ToString(file["path"])));
        string rollbackRoot = Path.Combine(data, "updates", "rollback", version + "-" + DateTime.UtcNow.ToString("yyyyMMddHHmmss"));
        Directory.CreateDirectory(rollbackRoot);
        string oldManifestCopy = Path.Combine(rollbackRoot, ManifestName);
        File.Copy(Path.Combine(install, ManifestName), oldManifestCopy, true);
        foreach (string relative in touched)
        {
            string dst = Safe(install, relative);
            if (File.Exists(dst))
            {
                string saved = Safe(rollbackRoot, relative);
                Directory.CreateDirectory(Path.GetDirectoryName(saved));
                File.Copy(dst, saved, true);
            }
        }
        string previousDisplayVersion = null;

        try
        {
            foreach (string relative in oldFiles)
            {
                if (String.Equals(relative, "Uninstall.exe", StringComparison.OrdinalIgnoreCase) || newNames.Contains(relative)) continue;
                string obsolete = Safe(install, relative);
                if (File.Exists(obsolete)) File.Delete(obsolete);
            }
            foreach (var file in newFiles)
            {
                string relative = Convert.ToString(file["path"]);
                string src = Path.Combine(stage, relative.Replace('/', Path.DirectorySeparatorChar));
                string dst = Safe(install, relative);
                Directory.CreateDirectory(Path.GetDirectoryName(dst));
                File.Copy(src, dst, true);
                if (!String.Equals(HashFile(dst), Convert.ToString(file["sha256"]), StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("写入安装目录后的校验失败：" + relative);
            }
            var manifestLines = new List<string> { ManifestHeader, install };
            manifestLines.AddRange(newFiles.Select(f => Convert.ToString(f["path"])));
            manifestLines.Add("Uninstall.exe");
            string temp = Path.Combine(install, ManifestName + ".tmp");
            File.WriteAllLines(temp, manifestLines, new UTF8Encoding(false));
            File.Copy(temp, Path.Combine(install, ManifestName), true);
            File.Delete(temp);
            using (RegistryKey key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\EasyTools", true))
            {
                if (key != null)
                {
                    previousDisplayVersion = Convert.ToString(key.GetValue("DisplayVersion", ""));
                    key.SetValue("DisplayVersion", version, RegistryValueKind.String);
                }
            }

            string exe = Path.Combine(install, AppName + ".exe");
            Process launched = Process.Start(new ProcessStartInfo(exe) { WorkingDirectory = install, UseShellExecute = true });
            if (launched == null || launched.WaitForExit(15000))
                throw new InvalidOperationException("新版本启动后很快退出，已自动恢复旧版本。");
            return 0;
        }
        catch
        {
            Restore(install, rollbackRoot, touched, oldManifestCopy);
            if (previousDisplayVersion != null)
            {
                using (RegistryKey key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\EasyTools", true))
                    if (key != null) key.SetValue("DisplayVersion", previousDisplayVersion, RegistryValueKind.String);
            }
            string exe = Path.Combine(install, AppName + ".exe");
            if (File.Exists(exe)) Process.Start(new ProcessStartInfo(exe) { WorkingDirectory = install, UseShellExecute = true });
            throw;
        }
    }

    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            if (args.Length != 4 || args[0] != "--apply")
                throw new ArgumentException("更新助手启动参数无效。");
            return Apply(Int32.Parse(args[1]), args[2], args[3], Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData) + Path.DirectorySeparatorChar + AppName);
        }
        catch (Exception ex)
        {
            string data = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            try { File.AppendAllText(Path.Combine(data, AppName, "update-helper.log"), DateTime.Now.ToString("s") + " " + ex + Environment.NewLine, Encoding.UTF8); }
            catch { }
            MessageBox.Show("Easy-Tools 更新未能完成。旧版本已保留或已恢复。\n\n" + ex.Message,
                "Easy-Tools 更新", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 1;
        }
    }
}
