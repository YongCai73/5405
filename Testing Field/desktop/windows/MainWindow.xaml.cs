using Microsoft.Web.WebView2.Core;
using Microsoft.Win32;
using System.Collections.Concurrent;
using System.IO;
using System.Text.Json.Nodes;
using System.Windows;

namespace MeetingCopilot.Windows;

public partial class MainWindow : Window
{
    private static readonly HashSet<string> Actions = new(StringComparer.Ordinal)
    {
        "bootstrap", "meeting", "demo", "create", "ask", "summary", "export", "importAudio",
        "devices", "startRecording", "stopRecording", "beginQuestion", "resumeQuestion", "questionHistory", "correctTranscript",
        "skills", "feedback", "preferences", "suggest", "settings", "saveModelSettings", "testModel", "deleteApiKey",
    };
    private readonly BackendClient backend = new();
    private readonly ConcurrentDictionary<string, FileTransfer> transfers = new();
    private readonly ConcurrentDictionary<string, byte> pending = new();
    private string? root;

    public MainWindow()
    {
        InitializeComponent();
        Loaded += StartAsync;
        Closing += CloseAsync;
        backend.Reply += reply => Dispatcher.InvokeAsync(() => ReplyAsync(reply));
        backend.Stopped += () => Dispatcher.InvokeAsync(BackendStopped);
    }

    private async void StartAsync(object sender, RoutedEventArgs args)
    {
        try
        {
            root = BackendPaths.Root();
            await backend.StartAsync(root, CancellationToken.None);
            await Web.EnsureCoreWebView2Async();
            Web.CoreWebView2.WebMessageReceived += WebMessageReceived;
            Web.CoreWebView2.NavigationStarting += NavigationStarting;
            await Web.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync("""
                window.webkit ??= {};
                window.webkit.messageHandlers ??= {};
                window.webkit.messageHandlers.copilot = {
                    postMessage: (message) => window.chrome.webview.postMessage(message)
                };
                """);
            Web.Source = new Uri(Path.Combine(root, "desktop", "ui", "index.html"));
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "Meeting Copilot couldn't open", MessageBoxButton.OK, MessageBoxImage.Error);
            Close();
        }
    }

    private void NavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs args)
    {
        var expected = new Uri(Path.Combine(root!, "desktop", "ui", "index.html")).AbsoluteUri;
        args.Cancel = !string.Equals(args.Uri, expected, StringComparison.OrdinalIgnoreCase);
    }

    private async void WebMessageReceived(object? sender, CoreWebView2WebMessageReceivedEventArgs args)
    {
        JsonObject? request;
        try { request = JsonNode.Parse(args.WebMessageAsJson) as JsonObject; }
        catch (System.Text.Json.JsonException) { return; }
        var id = Text(request?["id"]);
        var action = Text(request?["action"]);
        if (request is null
            || id is not { Length: > 0 and <= 80 }
            || action is null || !Actions.Contains(action)
            || request["payload"] is not JsonObject payload)
            return;

        try
        {
            if (action is "importAudio" or "export")
            {
                if (!await ChooseTransferAsync(id, action, payload)) return;
                return;
            }
            pending.TryAdd(id, 0);
            await backend.SendAsync(request);
        }
        catch (Exception error)
        {
            pending.TryRemove(id, out _);
            await DeliverAsync(new JsonObject { ["id"] = id, ["ok"] = false, ["error"] = SafeError(error) });
        }
    }

    private async Task<bool> ChooseTransferAsync(string id, string action, JsonObject payload)
    {
        var meeting = Text(payload["meetingId"]) ?? "";
        string? selected = action == "importAudio" ? PickAudio() : PickExport();
        if (selected is null)
        {
            await DeliverAsync(new JsonObject { ["id"] = id, ["ok"] = true, ["result"] = new JsonObject { ["cancelled"] = true } });
            return false;
        }
        var folder = Path.Combine(Path.GetTempPath(), "MeetingCopilot", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(folder);
        var temporary = Path.Combine(folder, action == "importAudio" ? Path.GetFileName(selected) : "meeting.json");
        if (action == "importAudio") File.Copy(selected, temporary, overwrite: false);
        transfers[id] = new FileTransfer(folder, temporary, action == "export" ? selected : null);
        pending.TryAdd(id, 0);
        await backend.SendAsync(new JsonObject
        {
            ["id"] = id, ["action"] = action,
            ["payload"] = new JsonObject { ["meetingId"] = meeting, ["path"] = temporary },
        });
        return true;
    }

    private static string? PickAudio()
    {
        var dialog = new OpenFileDialog { Title = "Import meeting audio", Filter = "Audio and video|*.wav;*.mp3;*.m4a;*.mp4;*.flac;*.ogg;*.webm|All files|*.*", Multiselect = false };
        return dialog.ShowDialog() == true ? dialog.FileName : null;
    }

    private static string? PickExport()
    {
        var dialog = new SaveFileDialog { Title = "Export meeting", Filter = "JSON|*.json", DefaultExt = ".json", FileName = "meeting-export.json" };
        return dialog.ShowDialog() == true ? dialog.FileName : null;
    }

    private async Task ReplyAsync(JsonObject reply)
    {
        var id = Text(reply["id"]);
        if (id is null) return;
        pending.TryRemove(id, out _);
        if (transfers.TryRemove(id, out var transfer))
        {
            try
            {
                if (transfer.ExportDestination is not null && reply["ok"] is JsonValue ok
                    && ok.TryGetValue<bool>(out var succeeded) && succeeded)
                {
                    File.Copy(transfer.TemporaryPath, transfer.ExportDestination, overwrite: true);
                    reply["result"] = new JsonObject { ["path"] = transfer.ExportDestination };
                }
            }
            catch (IOException)
            {
                reply = new JsonObject { ["id"] = id, ["ok"] = false, ["error"] = "Could not save the export to the selected folder." };
            }
            finally { try { Directory.Delete(transfer.Folder, recursive: true); } catch (IOException) { } }
        }
        await DeliverAsync(reply);
    }

    private async Task DeliverAsync(JsonObject reply)
    {
        if (Web.CoreWebView2 is null) return;
        await Web.CoreWebView2.ExecuteScriptAsync($"window.copilotReceive({reply.ToJsonString()});");
    }

    private void BackendStopped()
    {
        foreach (var id in pending.Keys)
        {
            pending.TryRemove(id, out _);
            _ = DeliverAsync(new JsonObject { ["id"] = id, ["ok"] = false, ["error"] = "The meeting backend stopped. Reopen the app to retry." });
        }
    }

    private async void CloseAsync(object? sender, System.ComponentModel.CancelEventArgs args) => await backend.DisposeAsync();

    private static string SafeError(Exception error) => error is InvalidOperationException ? error.Message : "Could not complete the desktop request.";

    private static string? Text(JsonNode? node) => node is JsonValue value
        && value.TryGetValue<string>(out var text) ? text : null;

    private sealed record FileTransfer(string Folder, string TemporaryPath, string? ExportDestination);
}
