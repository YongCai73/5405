using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace MeetingCopilot.Windows;

/// <summary>Owns the local-only JSON Lines connection to desktop/bridge.py.</summary>
internal sealed class BackendClient : IAsyncDisposable
{
    private readonly SemaphoreSlim writeLock = new(1, 1);
    private readonly CancellationTokenSource cancellation = new();
    private Process? process;
    private StreamWriter? input;

    public event Action<JsonObject>? Reply;
    public event Action? Stopped;

    public async Task StartAsync(string root, CancellationToken token)
    {
        var python = BackendPaths.Python(root);
        var script = Path.Combine(root, "desktop", "bridge.py");
        var dataRoot = Environment.GetEnvironmentVariable("MEETING_COPILOT_DESKTOP_DATA_DIR")
            ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                            "Meeting Copilot", "Workspace");
        if (!Path.IsPathFullyQualified(dataRoot))
            throw new InvalidOperationException("MEETING_COPILOT_DESKTOP_DATA_DIR must be an absolute path.");

        var start = new ProcessStartInfo(python)
        {
            WorkingDirectory = root,
            UseShellExecute = false,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };
        start.ArgumentList.Add("-u");
        start.ArgumentList.Add(script);
        start.ArgumentList.Add("--data-dir");
        start.ArgumentList.Add(dataRoot);
        var config = Environment.GetEnvironmentVariable("MEETING_COPILOT_DESKTOP_CONFIG");
        if (!string.IsNullOrWhiteSpace(config))
        {
            if (!Path.IsPathFullyQualified(config))
                throw new InvalidOperationException("MEETING_COPILOT_DESKTOP_CONFIG must be an absolute path.");
            start.ArgumentList.Add("--config");
            start.ArgumentList.Add(config);
        }
        start.Environment["PYTHONPATH"] = Path.Combine(root, "src");
        start.Environment.Remove("PYTHONHOME");

        process = Process.Start(start) ?? throw new InvalidOperationException("Could not start the local Python backend.");
        input = process.StandardInput;
        input.AutoFlush = true;
        _ = ReadRepliesAsync(process.StandardOutput, cancellation.Token);
        _ = DrainDiagnosticsAsync(process.StandardError, cancellation.Token);
        _ = WaitForExitAsync(process, cancellation.Token);
        await Task.Yield();
        token.ThrowIfCancellationRequested();
    }

    public async Task SendAsync(JsonObject request)
    {
        if (input is null || process is not { HasExited: false })
            throw new InvalidOperationException("The meeting backend is unavailable.");
        await writeLock.WaitAsync(cancellation.Token);
        try
        {
            await input.WriteLineAsync(request.ToJsonString());
        }
        finally
        {
            writeLock.Release();
        }
    }

    private async Task ReadRepliesAsync(StreamReader output, CancellationToken token)
    {
        try
        {
            while (!token.IsCancellationRequested && await output.ReadLineAsync(token) is { } line)
            {
                if (JsonNode.Parse(line) is JsonObject reply)
                    Reply?.Invoke(reply);
            }
        }
        catch (OperationCanceledException) { }
        catch (JsonException) { }
    }

    private static async Task DrainDiagnosticsAsync(StreamReader diagnostics, CancellationToken token)
    {
        try
        {
            while (!token.IsCancellationRequested && await diagnostics.ReadLineAsync(token) is not null) { }
        }
        catch (OperationCanceledException) { }
    }

    private async Task WaitForExitAsync(Process running, CancellationToken token)
    {
        try { await running.WaitForExitAsync(token); }
        catch (OperationCanceledException) { return; }
        Stopped?.Invoke();
    }

    public async ValueTask DisposeAsync()
    {
        cancellation.Cancel();
        if (process is { HasExited: false })
        {
            try { input?.Close(); }
            catch (IOException) { }
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(20));
            try { await process.WaitForExitAsync(timeout.Token); }
            catch (OperationCanceledException) { process.Kill(entireProcessTree: true); }
        }
        input?.Dispose();
        process?.Dispose();
        cancellation.Dispose();
        writeLock.Dispose();
    }
}

internal static class BackendPaths
{
    public static string Root()
    {
        var configured = Environment.GetEnvironmentVariable("MEETING_COPILOT_DESKTOP_ROOT");
        if (!string.IsNullOrWhiteSpace(configured))
            return ValidRoot(configured) ? Path.GetFullPath(configured) : throw new InvalidOperationException(
                "MEETING_COPILOT_DESKTOP_ROOT must contain desktop\\bridge.py and src.");

        var bundlePointer = Path.Combine(AppContext.BaseDirectory, "backend-root.txt");
        if (File.Exists(bundlePointer))
        {
            var value = File.ReadAllText(bundlePointer).Trim();
            var bundled = Path.IsPathFullyQualified(value)
                ? value
                : Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, value));
            if (ValidRoot(bundled)) return bundled;
            throw new InvalidOperationException("The Windows desktop bundle is missing its Python runtime.");
        }

        foreach (var start in new[] { AppContext.BaseDirectory, Directory.GetCurrentDirectory() })
        {
            for (var directory = new DirectoryInfo(start); directory is not null; directory = directory.Parent)
            {
                if (ValidRoot(directory.FullName)) return directory.FullName;
            }
        }
        throw new InvalidOperationException(
            "Could not find the Meeting Copilot project root. Set MEETING_COPILOT_DESKTOP_ROOT.");
    }

    public static string Python(string root)
    {
        var configured = Environment.GetEnvironmentVariable("MEETING_COPILOT_PYTHON");
        if (!string.IsNullOrWhiteSpace(configured) && File.Exists(configured)) return configured;
        var projectVenv = Path.Combine(root, ".venv", "Scripts", "python.exe");
        if (File.Exists(projectVenv)) return projectVenv;
        throw new InvalidOperationException(
            "Could not find a project Python environment. Set MEETING_COPILOT_PYTHON to its absolute python.exe path.");
    }

    private static bool ValidRoot(string root) =>
        File.Exists(Path.Combine(root, "desktop", "bridge.py")) && Directory.Exists(Path.Combine(root, "src"));
}
