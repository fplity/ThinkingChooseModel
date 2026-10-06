param(
    [switch]$Preview,
    [ValidateSet(1.0, 1.25, 1.5)]
    [double]$Scale = 1.0,
    [string]$TargetContact = '陈颢予'
)

$ErrorActionPreference = 'Stop'
$script:PreviewMode = [bool]$Preview
$script:PreviewScale = $Scale
$script:TargetContact = $TargetContact.Trim()
if ([string]::IsNullOrWhiteSpace($script:TargetContact) -or $script:TargetContact.Length -gt 80 -or $script:TargetContact.IndexOfAny([char[]]([char]0, [char]9, [char]10, [char]13)) -ge 0) {
    throw 'TargetContact must be a non-empty chat name of at most 80 characters.'
}
$env:LAYA_TARGET_CONTACT = $script:TargetContact
$script:WorkerBridge = $null
$script:PollTimer = $null
$script:MonitorEnabled = $false
$script:WorkerReady = $false
$script:WorkerCandidates = @()
$script:StatusText = $null
$script:StatusDot = $null
$script:ToggleButton = $null
$script:CandidateViews = @()
$script:WorkerOutputQueue = $null
$script:Window = $null
$script:WindowHook = $null

$script:Palette = @{
    Window = '#F4F7FA'
    Surface = '#FFFFFF'
    Border = '#D8E1E9'
    Text = '#1E2B38'
    Muted = '#6C7C8C'
    Subtle = '#94A2AF'
    Line = '#E5EBF0'
    Row = '#FFFFFF'
    Button = '#F3F6F8'
    ButtonEdge = '#D4DFE8'
    Primary = '#527B9E'
    PrimarySoft = '#E7EFF5'
    PrimaryEdge = '#9CB8CD'
    Green = '#5E9B89'
    GreenDark = '#E6F1ED'
    GreenEdge = '#B8D6CA'
    Amber = '#B9854F'
    Red = '#C86D7C'
}

$script:FixedTemplates = @(
    [pscustomobject]@{ Id = 'candidate_1'; Label = '自然接话'; Text = '听起来不错，我们再约个时间。'; Score = $null },
    [pscustomobject]@{ Id = 'candidate_2'; Label = '顺势询问'; Text = '你更想选哪一天？'; Score = $null },
    [pscustomobject]@{ Id = 'candidate_3'; Label = '轻松回应'; Text = '好呀，等你方便时告诉我。'; Score = $null }
)
$script:WorkerTemplateAllowlistJson = '[{"label":"自然接话","text":"好呀，听起来不错。"},{"label":"顺势询问","text":"你想什么时候去？"},{"label":"礼貌拒绝","text":"这次先不去了。"},{"label":"缓一缓","text":"我先看看时间。"}]'
$script:WorkerTemplateAllowlist = ConvertFrom-Json -InputObject $script:WorkerTemplateAllowlistJson -ErrorAction Stop

$script:WorkerStatusText = @{
    worker_ready = '等待目标聊天'
    starting = '正在启动本机 OCR 和 Laya…'
    monitoring = '监测中 · 仅检查指定会话'
    paused = '已暂停 · 不会读取屏幕'
    foreground_required = '请将指定微信聊天置于前台'
    wrong_header = '当前标题未确认 · 未读取聊天正文'
    session_changed = '检测到会话变化 · 已锁定并暂停'
    foreground_changed = '窗口焦点变化 · 本次扫描已停止'
    short_body = '会话已确认 · 等待可读内容'
    analyzing = '回复建议已更新'
    copied = '已复制固定回复'
    copied_no_caret = '已复制 · 输入框无光标，未填入'
    draft_filled = '草稿已填入 · 不会自动发送'
    not_ready = '监测未就绪 · 未填入草稿'
    clipboard_failed = '无法访问剪贴板 · 未填入草稿'
    model_error = '本地识别初始化失败'
    runtime_error = '本地识别暂时不可用'
    invalid_command = '收到无效本地命令'
}

Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Xaml

if (-not ('LayaDesktop.NativeWindow' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace LayaDesktop {
    public static class NativeWindow {
        [DllImport("user32.dll", EntryPoint="GetWindowLongPtrW", SetLastError=true)]
        private static extern IntPtr GetWindowLongPtr64(IntPtr hwnd, int index);
        [DllImport("user32.dll", EntryPoint="GetWindowLongW", SetLastError=true)]
        private static extern int GetWindowLong32(IntPtr hwnd, int index);
        [DllImport("user32.dll", EntryPoint="SetWindowLongPtrW", SetLastError=true)]
        private static extern IntPtr SetWindowLongPtr64(IntPtr hwnd, int index, IntPtr value);
        [DllImport("user32.dll", EntryPoint="SetWindowLongW", SetLastError=true)]
        private static extern int SetWindowLong32(IntPtr hwnd, int index, int value);
        [DllImport("user32.dll", SetLastError=true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool SetWindowPos(IntPtr hwnd, IntPtr insertAfter, int x, int y, int cx, int cy, uint flags);

        public static IntPtr GetWindowLongPtr(IntPtr hwnd, int index) {
            return IntPtr.Size == 8 ? GetWindowLongPtr64(hwnd, index) : new IntPtr(GetWindowLong32(hwnd, index));
        }
        public static IntPtr SetWindowLongPtr(IntPtr hwnd, int index, IntPtr value) {
            return IntPtr.Size == 8 ? SetWindowLongPtr64(hwnd, index, value) : new IntPtr(SetWindowLong32(hwnd, index, value.ToInt32()));
        }
        public static void SetNoActivate(IntPtr hwnd) {
            const int GWL_EXSTYLE = -20;
            const long WS_EX_NOACTIVATE = 0x08000000L;
            const long WS_EX_TOOLWINDOW = 0x00000080L;
            long style = GetWindowLongPtr(hwnd, GWL_EXSTYLE).ToInt64();
            SetWindowLongPtr(hwnd, GWL_EXSTYLE, new IntPtr(style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW));
            SetWindowPos(hwnd, new IntPtr(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010);
        }
    }
}
'@
}

if (-not ('LayaDesktop.WorkerBridge' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace LayaDesktop {
    public sealed class WorkerBridge : IDisposable {
        private readonly Process process;
        private readonly ConcurrentQueue<string> output = new ConcurrentQueue<string>();
        private readonly object inputLock = new object();
        private Task outputTask;
        private Task errorTask;

        public WorkerBridge(string pythonExe, string workerPath, int overlayHwnd, string workingDirectory) {
            var start = new ProcessStartInfo();
            start.FileName = pythonExe;
            start.Arguments = "-B \"" + workerPath + "\" --overlay-hwnd " + overlayHwnd.ToString();
            start.WorkingDirectory = workingDirectory;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.RedirectStandardInput = true;
            start.RedirectStandardOutput = true;
            start.RedirectStandardError = true;
            start.StandardOutputEncoding = new UTF8Encoding(false);
            start.StandardErrorEncoding = new UTF8Encoding(false);
            start.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
            start.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
            process = new Process();
            process.StartInfo = start;
            if (!process.Start()) throw new InvalidOperationException("worker_start_failed");
            outputTask = Task.Factory.StartNew(() => {
                string line;
                while ((line = process.StandardOutput.ReadLine()) != null) output.Enqueue(line);
            }, CancellationToken.None, TaskCreationOptions.LongRunning, TaskScheduler.Default);
            errorTask = Task.Factory.StartNew(() => {
                while (process.StandardError.ReadLine() != null) { }
            }, CancellationToken.None, TaskCreationOptions.LongRunning, TaskScheduler.Default);
        }

        public int ProcessId { get { return process.Id; } }
        public bool HasExited { get { try { return process.HasExited; } catch { return true; } } }
        public bool TryRead(out string line) { return output.TryDequeue(out line); }

        public bool Send(string command) {
            lock (inputLock) {
                try {
                    if (process.HasExited) return false;
                    process.StandardInput.WriteLine(command);
                    process.StandardInput.Flush();
                    return true;
                } catch { return false; }
            }
        }

        public void Stop() {
            if (process == null) return;
            try {
                if (!process.HasExited) {
                    Send("{\"v\":1,\"cmd\":\"shutdown\"}");
                    try { process.StandardInput.Close(); } catch { }
                    if (!process.WaitForExit(3000)) {
                        // This is the exact child process created by this host.
                        try { process.Kill(); process.WaitForExit(1500); } catch { }
                    }
                }
            } catch { }
        }

        public void Dispose() {
            Stop();
            try { process.Dispose(); } catch { }
        }
    }
}
'@
}

function Convert-Color([string]$Hex) {
    return [System.Windows.Media.ColorConverter]::ConvertFromString($Hex)
}

function New-Brush([string]$Hex) {
    return New-Object System.Windows.Media.SolidColorBrush (Convert-Color $Hex)
}

function Get-ScaledDip([double]$Value) {
    return $Value * $script:PreviewScale
}

function New-ScaledThickness([double]$Left, [double]$Top, [double]$Right, [double]$Bottom) {
    $scaledLeft = Get-ScaledDip $Left
    $scaledTop = Get-ScaledDip $Top
    $scaledRight = Get-ScaledDip $Right
    $scaledBottom = Get-ScaledDip $Bottom
    return [System.Windows.Thickness]::new($scaledLeft, $scaledTop, $scaledRight, $scaledBottom)
}

function Set-OpaqueText([System.Windows.Controls.TextBlock]$TextBlock, [string]$BackgroundHex) {
    $TextBlock.Background = New-Brush $BackgroundHex
    $TextBlock.SnapsToDevicePixels = $true
    [System.Windows.Media.TextOptions]::SetTextFormattingMode($TextBlock, [System.Windows.Media.TextFormattingMode]::Display)
    [System.Windows.Media.TextOptions]::SetTextRenderingMode($TextBlock, [System.Windows.Media.TextRenderingMode]::Auto)
}

function New-TextBlock([string]$Text, [double]$Size, [string]$Color, [string]$Background, [bool]$Bold = $false) {
    $textBlock = New-Object System.Windows.Controls.TextBlock
    $textBlock.Text = $Text
    $textBlock.FontFamily = New-Object System.Windows.Media.FontFamily('Microsoft YaHei UI')
    $textBlock.FontSize = Get-ScaledDip $Size
    $textBlock.Foreground = New-Brush $Color
    if ($Bold) { $textBlock.FontWeight = [System.Windows.FontWeights]::SemiBold }
    Set-OpaqueText $textBlock $Background
    return $textBlock
}

function New-ActionButton([string]$Text, [bool]$Primary = $false, [double]$Width = 54) {
    $button = New-Object System.Windows.Controls.Button
    $button.Content = $Text
    $button.Width = Get-ScaledDip $Width
    $button.Height = Get-ScaledDip 30
    $button.Padding = New-ScaledThickness 6 0 6 0
    $button.FontFamily = New-Object System.Windows.Media.FontFamily('Microsoft YaHei UI')
    $button.FontSize = Get-ScaledDip 12
    $button.FontWeight = [System.Windows.FontWeights]::SemiBold
    $button.Foreground = New-Brush $script:Palette.Text
    if ($Primary) {
        $button.Background = New-Brush $script:Palette.Primary
        $button.BorderBrush = New-Brush $script:Palette.Primary
        $button.Foreground = New-Brush '#FFFFFF'
    } else {
        $button.Background = New-Brush $script:Palette.Button
        $button.BorderBrush = New-Brush $script:Palette.ButtonEdge
    }
    $button.BorderThickness = [System.Windows.Thickness]::new((Get-ScaledDip 1))
    $button.Cursor = [System.Windows.Input.Cursors]::Hand
    $button.Focusable = $false
    $button.IsTabStop = $false
    $button.SnapsToDevicePixels = $true
    $cornerRadius = if ($Primary) { Get-ScaledDip 13 } else { Get-ScaledDip 9 }
    $templateMarkup = @"
<ControlTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" TargetType="{x:Type Button}">
  <Border x:Name="ButtonBorder" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="$cornerRadius" Padding="{TemplateBinding Padding}" SnapsToDevicePixels="True">
    <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center" RecognizesAccessKey="True" />
  </Border>
  <ControlTemplate.Triggers>
    <Trigger Property="IsEnabled" Value="False">
      <Setter TargetName="ButtonBorder" Property="Opacity" Value="0.5" />
    </Trigger>
    <Trigger Property="IsMouseOver" Value="True">
      <Setter TargetName="ButtonBorder" Property="Opacity" Value="0.86" />
    </Trigger>
    <Trigger Property="IsPressed" Value="True">
      <Setter TargetName="ButtonBorder" Property="Opacity" Value="0.68" />
    </Trigger>
  </ControlTemplate.Triggers>
</ControlTemplate>
"@
    $button.Template = [System.Windows.Markup.XamlReader]::Parse($templateMarkup)
    return $button
}

function Set-Status([string]$Text, [string]$Color = $script:Palette.Muted) {
    if ($script:StatusText) {
        $script:StatusText.Text = $Text
        $script:StatusText.Foreground = New-Brush $Color
    }
    if ($script:StatusDot) {
        $script:StatusDot.Fill = New-Brush $(if ($script:MonitorEnabled) { $script:Palette.Green } else { $script:Palette.Subtle })
    }
    if ($script:ToggleButton) {
        if ($script:PreviewMode) {
            $script:ToggleButton.Content = $(if ($script:MonitorEnabled) { '暂停演示' } else { '继续演示' })
        } else {
            $script:ToggleButton.Content = $(if ($script:MonitorEnabled) { '暂停监测' } else { '继续监测' })
        }
        $script:ToggleButton.Background = New-Brush $(if ($script:MonitorEnabled) { $script:Palette.GreenDark } else { $script:Palette.PrimarySoft })
        $script:ToggleButton.BorderBrush = New-Brush $(if ($script:MonitorEnabled) { $script:Palette.GreenEdge } else { $script:Palette.PrimaryEdge })
        $script:ToggleButton.Foreground = New-Brush $script:Palette.Text
    }
}

function Send-WorkerCommand([string]$Command, [string]$CandidateId = '') {
    if (-not $script:WorkerBridge) { return $false }
    switch ($Command) {
        'start' { $json = '{"v":1,"cmd":"start"}' }
        'pause' { $json = '{"v":1,"cmd":"pause"}' }
        'shutdown' { $json = '{"v":1,"cmd":"shutdown"}' }
        'fill' {
            if ($CandidateId -notin @('candidate_1', 'candidate_2', 'candidate_3')) { return $false }
            $json = '{"v":1,"cmd":"fill","candidate_id":"' + $CandidateId + '"}'
        }
        default { return $false }
    }
    return $script:WorkerBridge.Send($json)
}

function Invoke-CopyCandidate([int]$Index) {
    if ($Index -lt 0 -or $Index -ge $script:CandidateViews.Count) { return }
    $candidate = $script:CandidateViews[$Index].Candidate
    if ($script:PreviewMode) {
        Set-Status ('预览：第 ' + ($Index + 1) + ' 条复制操作已模拟') $script:Palette.Muted
        return
    }
    try {
        [System.Windows.Clipboard]::SetText([string]$candidate.Text)
        Set-Status ('已复制第 ' + ($Index + 1) + ' 条固定回复') $script:Palette.Green
    } catch {
        Set-Status '无法访问剪贴板' $script:Palette.Amber
    }
}

function Invoke-FillCandidate([int]$Index) {
    if ($Index -lt 0 -or $Index -ge $script:CandidateViews.Count) { return }
    if ($script:PreviewMode) {
        Set-Status ('预览：第 ' + ($Index + 1) + ' 条填入操作已模拟') $script:Palette.Muted
        return
    }
    if (-not $script:WorkerReady -or -not $script:MonitorEnabled) {
        Set-Status '监测未就绪 · 未填入草稿' $script:Palette.Amber
        return
    }
    $candidate = $script:CandidateViews[$Index].Candidate
    if (-not (Send-WorkerCommand 'fill' ([string]$candidate.Id))) {
        Set-Status '本地 worker 未响应 · 未填入草稿' $script:Palette.Amber
    } else {
        Set-Status '正在重新核对会话与输入框…' $script:Palette.Muted
    }
}

function Set-CandidateItems([object[]]$Items) {
    if ($Items.Count -ne 3) { return $false }
    $accepted = @()
    $seenIds = @{}
    foreach ($item in $Items) {
        if ($null -eq $item) { return $false }
        $candidateId = [string]$item.id
        if ($candidateId -cnotin @('candidate_1', 'candidate_2', 'candidate_3') -or $seenIds.ContainsKey($candidateId)) { return $false }
        $known = $script:WorkerTemplateAllowlist | Where-Object { $_.label -ceq [string]$item.label -and $_.text -ceq [string]$item.text } | Select-Object -First 1
        if ($null -eq $known) { return $false }
        $score = $null
        if ($null -ne $item.score) {
            if ($item.score -isnot [double] -and $item.score -isnot [int] -and $item.score -isnot [long]) { return $false }
            if ([double]$item.score -lt 0 -or [double]$item.score -gt 1) { return $false }
            $score = [double]$item.score
        }
        $accepted += [pscustomobject]@{ Id = $candidateId; Label = [string]$known.label; Text = [string]$known.text; Score = $score }
        $seenIds[$candidateId] = $true
    }
    if ($accepted.Count -ne 3) { return $false }
    for ($i = 0; $i -lt 3; $i++) {
        $view = $script:CandidateViews[$i]
        $view.Candidate = $accepted[$i]
        $view.Label.Text = $accepted[$i].Label
        $view.Text.Text = $accepted[$i].Text
        $view.Fill.IsEnabled = $true
        $view.Copy.IsEnabled = $true
    }
    $script:WorkerCandidates = $accepted
    return $true
}

function Invoke-WorkerEvent([string]$Line) {
    try { $eventData = ConvertFrom-Json -InputObject $Line -ErrorAction Stop } catch { return }
    if ($eventData.v -ne 1 -or $eventData.event -notin @('status', 'candidates')) { return }
    if ($eventData.event -eq 'candidates') {
        if ($eventData.code -ne 'analyzing' -or $null -eq $eventData.items) { return }
        if (-not (Set-CandidateItems -Items @($eventData.items))) { return }
        $script:WorkerReady = $true
        Set-Status '回复建议已更新' $script:Palette.Green
        return
    }
    $code = [string]$eventData.code
    if (-not $script:WorkerStatusText.ContainsKey($code)) { return }
    if ($code -eq 'monitoring') { $script:WorkerReady = $true; $script:MonitorEnabled = $true }
    if ($code -eq 'paused' -or $code -eq 'session_changed' -or $code -eq 'model_error' -or $code -eq 'runtime_error') { $script:MonitorEnabled = $false }
    $color = $script:Palette.Muted
    if ($code -eq 'session_changed' -or $code -eq 'wrong_header') { $color = $script:Palette.Amber }
    if ($code -eq 'draft_filled' -or $code -eq 'monitoring' -or $code -eq 'analyzing') { $color = $script:Palette.Green }
    if ($code -eq 'model_error' -or $code -eq 'runtime_error') { $color = $script:Palette.Red }
    Set-Status $script:WorkerStatusText[$code] $color
}

function New-CandidateRow([int]$Index, [object]$Candidate, [string]$CardBackground) {
    $row = New-Object System.Windows.Controls.Border
    $row.Background = New-Brush $script:Palette.Row
    $row.BorderBrush = New-Brush $script:Palette.Line
    $row.BorderThickness = [System.Windows.Thickness]::new((Get-ScaledDip 1))
    $row.CornerRadius = [System.Windows.CornerRadius]::new((Get-ScaledDip 10))
    $row.Padding = New-ScaledThickness 8 5 8 5
    $row.Margin = New-ScaledThickness 0 0 0 6
    $row.Height = Get-ScaledDip 64
    $grid = New-Object System.Windows.Controls.Grid
    $indexColumn = New-Object System.Windows.Controls.ColumnDefinition
    $indexColumn.Width = [System.Windows.GridLength]::new((Get-ScaledDip 28))
    $grid.ColumnDefinitions.Add($indexColumn)
    $grid.ColumnDefinitions.Add((New-Object System.Windows.Controls.ColumnDefinition))
    $copyColumn = New-Object System.Windows.Controls.ColumnDefinition
    $copyColumn.Width = [System.Windows.GridLength]::new((Get-ScaledDip 58))
    $grid.ColumnDefinitions.Add($copyColumn)
    $fillColumn = New-Object System.Windows.Controls.ColumnDefinition
    $fillColumn.Width = [System.Windows.GridLength]::new((Get-ScaledDip 58))
    $grid.ColumnDefinitions.Add($fillColumn)

    $indexBadge = New-Object System.Windows.Controls.Border
    $indexBadge.Width = Get-ScaledDip 22
    $indexBadge.Height = Get-ScaledDip 22
    $indexBadge.Background = New-Brush $script:Palette.PrimarySoft
    $indexBadge.BorderBrush = New-Brush $script:Palette.PrimaryEdge
    $indexBadge.BorderThickness = [System.Windows.Thickness]::new((Get-ScaledDip 1))
    $indexBadge.CornerRadius = [System.Windows.CornerRadius]::new((Get-ScaledDip 11))
    $indexBadge.HorizontalAlignment = [System.Windows.HorizontalAlignment]::Left
    $indexBadge.VerticalAlignment = [System.Windows.VerticalAlignment]::Center
    $indexText = New-TextBlock ('0' + ($Index + 1)) 10 $script:Palette.Primary $script:Palette.PrimarySoft $true
    $indexText.HorizontalAlignment = [System.Windows.HorizontalAlignment]::Center
    $indexText.VerticalAlignment = [System.Windows.VerticalAlignment]::Center
    $indexBadge.Child = $indexText

    $copy = New-ActionButton '复制' $false 52
    $fill = New-ActionButton '填入' $true 52
    $copy.HorizontalAlignment = [System.Windows.HorizontalAlignment]::Center
    $fill.HorizontalAlignment = [System.Windows.HorizontalAlignment]::Center
    if (-not $script:PreviewMode) { $fill.IsEnabled = $false }
    $stack = New-Object System.Windows.Controls.StackPanel
    $stack.Orientation = [System.Windows.Controls.Orientation]::Vertical
    $stack.VerticalAlignment = [System.Windows.VerticalAlignment]::Center
    $label = New-TextBlock $Candidate.Label 11 $script:Palette.Muted $script:Palette.Row $false
    $text = New-TextBlock $Candidate.Text 14 $script:Palette.Text $script:Palette.Row $true
    $text.TextWrapping = [System.Windows.TextWrapping]::Wrap
    $stack.Children.Add($label) | Out-Null
    $stack.Children.Add($text) | Out-Null
    [System.Windows.Controls.Grid]::SetColumn($indexBadge, 0)
    [System.Windows.Controls.Grid]::SetColumn($stack, 1)
    [System.Windows.Controls.Grid]::SetColumn($copy, 2)
    [System.Windows.Controls.Grid]::SetColumn($fill, 3)
    $grid.Children.Add($indexBadge) | Out-Null
    $grid.Children.Add($stack) | Out-Null
    $grid.Children.Add($copy) | Out-Null
    $grid.Children.Add($fill) | Out-Null
    $row.Child = $grid
    $copyIndex = $Index
    $copy.Add_Click({ param($sender, $args) Invoke-CopyCandidate $copyIndex }.GetNewClosure())
    $fillIndex = $Index
    $fill.Add_Click({ param($sender, $args) Invoke-FillCandidate $fillIndex }.GetNewClosure())
    return [pscustomobject]@{ Border = $row; Label = $label; Text = $text; Copy = $copy; Fill = $fill; Candidate = $Candidate }
}

function New-OverlayWindow {
    $window = New-Object System.Windows.Window
    $window.Title = 'LAYA · 会话助手'
    $window.Width = Get-ScaledDip 420
    $window.Height = Get-ScaledDip 430
    $window.MinWidth = $window.Width
    $window.MinHeight = $window.Height
    $window.MaxWidth = $window.Width
    $window.MaxHeight = $window.Height
    $window.WindowStyle = [System.Windows.WindowStyle]::None
    $window.ResizeMode = [System.Windows.ResizeMode]::NoResize
    $window.ShowInTaskbar = $false
    $window.ShowActivated = $false
    $window.Topmost = $true
    $window.AllowsTransparency = $true
    $window.Opacity = 1.0
    $window.Background = [System.Windows.Media.Brushes]::Transparent
    $window.FontFamily = New-Object System.Windows.Media.FontFamily('Microsoft YaHei UI')
    $window.UseLayoutRounding = $true
    $window.SnapsToDevicePixels = $true
    $window.SizeToContent = [System.Windows.SizeToContent]::Manual
    $window.WindowStartupLocation = [System.Windows.WindowStartupLocation]::CenterScreen

    $grid = New-Object System.Windows.Controls.Grid
    $grid.Width = Get-ScaledDip 420
    $grid.Height = Get-ScaledDip 430
    $grid.Background = [System.Windows.Media.Brushes]::Transparent
    $grid.SnapsToDevicePixels = $true
    $card = New-Object System.Windows.Controls.Border
    $card.Width = Get-ScaledDip 420
    $card.Height = Get-ScaledDip 430
    $card.Background = New-Brush $script:Palette.Window
    $card.BorderBrush = New-Brush $script:Palette.Border
    $card.BorderThickness = [System.Windows.Thickness]::new((Get-ScaledDip 1))
    $card.CornerRadius = [System.Windows.CornerRadius]::new((Get-ScaledDip 18))
    $card.Padding = New-ScaledThickness 18 16 18 16
    $card.SnapsToDevicePixels = $true
    $shadow = New-Object System.Windows.Media.Effects.DropShadowEffect
    $shadow.Color = [System.Windows.Media.ColorConverter]::ConvertFromString('#60758A')
    $shadow.Opacity = 0.16
    $shadow.BlurRadius = Get-ScaledDip 18
    $shadow.ShadowDepth = Get-ScaledDip 4
    $card.Effect = $shadow

    $body = New-Object System.Windows.Controls.Grid
    foreach ($height in @(38, 54, 36, 28, 216, 26)) {
        $definition = New-Object System.Windows.Controls.RowDefinition
        $scaledHeight = Get-ScaledDip $height
        $definition.Height = [System.Windows.GridLength]::new($scaledHeight)
        $body.RowDefinitions.Add($definition)
    }
    $body.SnapsToDevicePixels = $true

    # Header: compact brand lockup with the existing drag and close behavior.
    $header = New-Object System.Windows.Controls.Grid
    $header.Background = New-Brush $script:Palette.Window
    $brandColumn = New-Object System.Windows.Controls.ColumnDefinition
    $brandColumn.Width = [System.Windows.GridLength]::new((Get-ScaledDip 34))
    $header.ColumnDefinitions.Add($brandColumn)
    $header.ColumnDefinitions.Add((New-Object System.Windows.Controls.ColumnDefinition))
    $title = New-TextBlock 'LAYA 会话助手' 18 $script:Palette.Text $script:Palette.Window $true
    $title.VerticalAlignment = [System.Windows.VerticalAlignment]::Center
    $title.Cursor = [System.Windows.Input.Cursors]::SizeAll
    $title.Margin = New-ScaledThickness 6 0 0 0
    $closeColumn = New-Object System.Windows.Controls.ColumnDefinition
    $closeColumn.Width = [System.Windows.GridLength]::new((Get-ScaledDip 38))
    $header.ColumnDefinitions.Add($closeColumn)
    $brand = New-Object System.Windows.Controls.Border
    $brand.Width = Get-ScaledDip 28
    $brand.Height = Get-ScaledDip 28
    $brand.Background = New-Brush $script:Palette.PrimarySoft
    $brand.BorderBrush = New-Brush $script:Palette.PrimaryEdge
    $brand.BorderThickness = [System.Windows.Thickness]::new((Get-ScaledDip 1))
    $brand.CornerRadius = [System.Windows.CornerRadius]::new((Get-ScaledDip 14))
    $brand.HorizontalAlignment = [System.Windows.HorizontalAlignment]::Left
    $brand.VerticalAlignment = [System.Windows.VerticalAlignment]::Center
    $brandText = New-TextBlock 'L' 13 $script:Palette.Primary $script:Palette.PrimarySoft $true
    $brandText.HorizontalAlignment = [System.Windows.HorizontalAlignment]::Center
    $brandText.VerticalAlignment = [System.Windows.VerticalAlignment]::Center
    $brand.Child = $brandText
    [System.Windows.Controls.Grid]::SetColumn($brand, 0)
    [System.Windows.Controls.Grid]::SetColumn($title, 1)
    $header.Children.Add($brand) | Out-Null
    $header.Children.Add($title) | Out-Null

    $close = New-ActionButton '×' $false 30
    $close.Height = Get-ScaledDip 24
    $close.FontSize = Get-ScaledDip 18
    $close.Margin = New-ScaledThickness 4 0 0 0
    $close.HorizontalAlignment = [System.Windows.HorizontalAlignment]::Center
    [System.Windows.Controls.Grid]::SetColumn($close, 2)
    $header.Children.Add($close) | Out-Null
    $close.Add_Click({ $script:Window.Close() })
    $title.Add_MouseLeftButtonDown({
        if ($_.ChangedButton -eq [System.Windows.Input.MouseButton]::Left) { try { $script:Window.DragMove() } catch { } }
    })
    [System.Windows.Controls.Grid]::SetRow($header, 0)
    $body.Children.Add($header) | Out-Null

    # Neutral preview identity; only production names the configured target.
    $contactRow = New-Object System.Windows.Controls.Grid
    $contactRow.Background = New-Brush $script:Palette.Window
    $avatarColumn = New-Object System.Windows.Controls.ColumnDefinition
    $avatarColumn.Width = [System.Windows.GridLength]::new((Get-ScaledDip 38))
    $contactRow.ColumnDefinitions.Add($avatarColumn)
    $contactRow.ColumnDefinitions.Add((New-Object System.Windows.Controls.ColumnDefinition))
    $statusColumn = New-Object System.Windows.Controls.ColumnDefinition
    $statusColumn.Width = [System.Windows.GridLength]::Auto
    $contactRow.ColumnDefinitions.Add($statusColumn)
    $contactTitle = if ($script:PreviewMode) { '演示会话' } else { $script:TargetContact }
    $avatar = New-Object System.Windows.Controls.Border
    $avatar.Width = Get-ScaledDip 32
    $avatar.Height = Get-ScaledDip 32
    $avatar.Background = New-Brush $script:Palette.PrimarySoft
    $avatar.BorderBrush = New-Brush $script:Palette.PrimaryEdge
    $avatar.BorderThickness = [System.Windows.Thickness]::new((Get-ScaledDip 1))
    $avatar.CornerRadius = [System.Windows.CornerRadius]::new((Get-ScaledDip 16))
    $avatar.VerticalAlignment = [System.Windows.VerticalAlignment]::Center
    $avatarText = New-TextBlock $(if ($script:PreviewMode) { '演' } else { $script:TargetContact.Substring(0, 1) }) 14 $script:Palette.Primary $script:Palette.PrimarySoft $true
    $avatarText.HorizontalAlignment = [System.Windows.HorizontalAlignment]::Center
    $avatarText.VerticalAlignment = [System.Windows.VerticalAlignment]::Center
    $avatar.Child = $avatarText
    [System.Windows.Controls.Grid]::SetColumn($avatar, 0)
    $contactRow.Children.Add($avatar) | Out-Null

    $contactStack = New-Object System.Windows.Controls.StackPanel
    $contactStack.Orientation = [System.Windows.Controls.Orientation]::Vertical
    $contactStack.VerticalAlignment = [System.Windows.VerticalAlignment]::Center
    $contactName = New-TextBlock $contactTitle 16 $script:Palette.Text $script:Palette.Window $true
    $contactMeta = New-TextBlock '专属会话 · 本地识别' 11 $script:Palette.Muted $script:Palette.Window $false
    $contactStack.Children.Add($contactName) | Out-Null
    $contactStack.Children.Add($contactMeta) | Out-Null
    [System.Windows.Controls.Grid]::SetColumn($contactStack, 1)
    $contactRow.Children.Add($contactStack) | Out-Null

    $statusStack = New-Object System.Windows.Controls.StackPanel
    $statusStack.Orientation = [System.Windows.Controls.Orientation]::Horizontal
    $statusStack.VerticalAlignment = [System.Windows.VerticalAlignment]::Center
    $script:StatusDot = New-Object System.Windows.Shapes.Ellipse
    $script:StatusDot.Width = Get-ScaledDip 8
    $script:StatusDot.Height = Get-ScaledDip 8
    $script:StatusDot.Fill = New-Brush $(if ($script:PreviewMode) { $script:Palette.Green } else { $script:Palette.Subtle })
    $script:StatusDot.VerticalAlignment = [System.Windows.VerticalAlignment]::Center
    $script:StatusText = New-TextBlock $(if ($script:PreviewMode) { '演示就绪' } else { '等待目标聊天' }) 11 $script:Palette.Muted $script:Palette.Window $true
    $script:StatusText.Margin = New-ScaledThickness 6 0 0 0
    $script:StatusText.MaxWidth = Get-ScaledDip 154
    $script:StatusText.TextTrimming = [System.Windows.TextTrimming]::CharacterEllipsis
    $script:StatusText.VerticalAlignment = [System.Windows.VerticalAlignment]::Center
    $statusStack.Children.Add($script:StatusDot) | Out-Null
    $statusStack.Children.Add($script:StatusText) | Out-Null
    [System.Windows.Controls.Grid]::SetColumn($statusStack, 2)
    $contactRow.Children.Add($statusStack) | Out-Null
    [System.Windows.Controls.Grid]::SetRow($contactRow, 1)
    $body.Children.Add($contactRow) | Out-Null

    # Monitoring action stays inline so the safety state remains visible without dominating the card.
    $monitorRow = New-Object System.Windows.Controls.Border
    $monitorRow.Background = New-Brush $script:Palette.PrimarySoft
    $monitorRow.BorderBrush = New-Brush $script:Palette.Line
    $monitorRow.BorderThickness = [System.Windows.Thickness]::new((Get-ScaledDip 1))
    $monitorRow.CornerRadius = [System.Windows.CornerRadius]::new((Get-ScaledDip 10))
    $monitorRow.Padding = New-ScaledThickness 10 2 8 2
    $monitorGrid = New-Object System.Windows.Controls.Grid
    $monitorLabel = New-TextBlock '会话安全检查' 11 $script:Palette.Muted $script:Palette.PrimarySoft $false
    $monitorLabel.VerticalAlignment = [System.Windows.VerticalAlignment]::Center
    $monitorButtonColumn = New-Object System.Windows.Controls.ColumnDefinition
    $monitorButtonColumn.Width = [System.Windows.GridLength]::new((Get-ScaledDip 88))
    $monitorGrid.ColumnDefinitions.Add((New-Object System.Windows.Controls.ColumnDefinition))
    $monitorGrid.ColumnDefinitions.Add($monitorButtonColumn)
    $script:ToggleButton = New-ActionButton $(if ($script:PreviewMode) { '继续演示' } else { '继续监测' }) $false 82
    $script:ToggleButton.Height = Get-ScaledDip 26
    $script:ToggleButton.HorizontalAlignment = [System.Windows.HorizontalAlignment]::Right
    [System.Windows.Controls.Grid]::SetColumn($script:ToggleButton, 1)
    $monitorGrid.Children.Add($monitorLabel) | Out-Null
    $monitorGrid.Children.Add($script:ToggleButton) | Out-Null
    $monitorRow.Child = $monitorGrid
    [System.Windows.Controls.Grid]::SetRow($monitorRow, 2)
    $body.Children.Add($monitorRow) | Out-Null
    $script:ToggleButton.Add_Click({
        if ($script:PreviewMode) {
            $script:MonitorEnabled = -not $script:MonitorEnabled
            Set-Status $(if ($script:MonitorEnabled) { '预览演示已继续' } else { '预览演示已暂停' }) $script:Palette.Muted
        } elseif ($script:MonitorEnabled) {
            $script:MonitorEnabled = $false
            [void](Send-WorkerCommand 'pause')
            Set-Status '已暂停 · 不会读取屏幕' $script:Palette.Muted
        } else {
            $script:MonitorEnabled = $true
            [void](Send-WorkerCommand 'start')
            Set-Status '正在重新启动监测…' $script:Palette.Muted
        }
    })
    $candidateHeader = New-Object System.Windows.Controls.Grid
    $candidateHeader.Background = New-Brush $script:Palette.Window
    $candidateHeader.ColumnDefinitions.Add((New-Object System.Windows.Controls.ColumnDefinition))
    $countColumn = New-Object System.Windows.Controls.ColumnDefinition
    $countColumn.Width = [System.Windows.GridLength]::Auto
    $candidateHeader.ColumnDefinitions.Add($countColumn)
    $candidateTitle = New-TextBlock '回复建议' 15 $script:Palette.Text $script:Palette.Window $true
    $candidateSource = New-TextBlock '本地固定模板 · 3 条' 11 $script:Palette.Muted $script:Palette.Window $false
    $candidateSource.VerticalAlignment = [System.Windows.VerticalAlignment]::Center
    [System.Windows.Controls.Grid]::SetColumn($candidateSource, 1)
    $candidateHeader.Children.Add($candidateTitle) | Out-Null
    $candidateHeader.Children.Add($candidateSource) | Out-Null
    [System.Windows.Controls.Grid]::SetRow($candidateHeader, 3)
    $body.Children.Add($candidateHeader) | Out-Null

    $rows = New-Object System.Windows.Controls.StackPanel
    $rows.Orientation = [System.Windows.Controls.Orientation]::Vertical
    $rows.Background = New-Brush $script:Palette.Window
    for ($i = 0; $i -lt 3; $i++) {
        $rowView = New-CandidateRow $i $script:FixedTemplates[$i] $script:Palette.Window
        $rows.Children.Add($rowView.Border) | Out-Null
        $script:CandidateViews += $rowView
    }
    [System.Windows.Controls.Grid]::SetRow($rows, 4)
    $body.Children.Add($rows) | Out-Null

    $footer = New-Object System.Windows.Controls.Grid
    $footer.Background = New-Brush $script:Palette.Window
    $footer.ColumnDefinitions.Add((New-Object System.Windows.Controls.ColumnDefinition))
    $footerHintColumn = New-Object System.Windows.Controls.ColumnDefinition
    $footerHintColumn.Width = [System.Windows.GridLength]::Auto
    $footer.ColumnDefinitions.Add($footerHintColumn)
    $footerText = New-TextBlock '填入只会保存草稿，不会自动发送' 11 $script:Palette.Muted $script:Palette.Window $false
    $footerText.VerticalAlignment = [System.Windows.VerticalAlignment]::Center
    $footer.Children.Add($footerText) | Out-Null
    $footerBadge = New-TextBlock 'LOCAL · PRIVATE' 10 $script:Palette.Subtle $script:Palette.Window $true
    $footerBadge.VerticalAlignment = [System.Windows.VerticalAlignment]::Center
    [System.Windows.Controls.Grid]::SetColumn($footerBadge, 1)
    $footer.Children.Add($footerBadge) | Out-Null
    [System.Windows.Controls.Grid]::SetRow($footer, 5)
    $body.Children.Add($footer) | Out-Null

    $card.Child = $body
    $grid.Children.Add($card) | Out-Null
    $window.Content = $grid
    return $window
}

function Start-Worker([IntPtr]$Hwnd) {
    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $runtime = Join-Path $PSScriptRoot 'laya_runtime.py'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or -not (Test-Path -LiteralPath $runtime -PathType Leaf)) {
        Set-Status 'Laya 本机运行环境不可用' $script:Palette.Red
        return
    }
    try {
        $script:WorkerOutputQueue = New-Object 'System.Collections.Concurrent.ConcurrentQueue[string]'
        $script:WorkerBridge = New-Object LayaDesktop.WorkerBridge($python, $runtime, $Hwnd.ToInt32(), $PSScriptRoot)
        $script:PollTimer = New-Object System.Windows.Threading.DispatcherTimer
        $script:PollTimer.Interval = [TimeSpan]::FromMilliseconds(100)
        $script:PollTimer.Add_Tick({
            if (-not $script:WorkerBridge) { return }
            $line = $null
            while ($script:WorkerBridge.TryRead([ref]$line)) {
                Invoke-WorkerEvent ([string]$line)
                $line = $null
            }
            if ($script:WorkerBridge.HasExited -and $script:MonitorEnabled) {
                $script:MonitorEnabled = $false
                Set-Status '本地 worker 已退出' $script:Palette.Red
            }
        })
        $script:PollTimer.Start()
        $script:MonitorEnabled = $true
        [void](Send-WorkerCommand 'start')
        Set-Status '正在启动本机 OCR 和 Laya…' $script:Palette.Muted
    } catch {
        $script:MonitorEnabled = $false
        Set-Status '无法启动本机 worker' $script:Palette.Red
    }
}

$script:Window = New-OverlayWindow
$script:Window.Add_SourceInitialized({
    $helper = New-Object System.Windows.Interop.WindowInteropHelper($script:Window)
    [LayaDesktop.NativeWindow]::SetNoActivate($helper.Handle)
    $source = [System.Windows.Interop.HwndSource]::FromHwnd($helper.Handle)
    $script:WindowHook = [System.Windows.Interop.HwndSourceHook]{
        param($hwnd, $message, $wParam, $lParam, [ref]$handled)
        if (($message -eq 0x0100 -or $message -eq 0x0104) -and $wParam.ToInt64() -eq 0x1B) {
            $handled.Value = $true
            $script:Window.Dispatcher.BeginInvoke([System.Action]{ $script:Window.Close() }) | Out-Null
            return [IntPtr]::Zero
        }
        if ($message -eq 0x0021) {
            $handled.Value = $true
            return [IntPtr]3
        }
        return [IntPtr]::Zero
    }
    $source.AddHook($script:WindowHook)
})
$script:Window.Add_KeyDown({
    if ($_.Key -eq [System.Windows.Input.Key]::Escape) { $script:Window.Close() }
})
$script:Window.Add_Closing({
    if ($script:PollTimer) { $script:PollTimer.Stop() }
    if ($script:WorkerBridge) {
        $script:WorkerBridge.Dispose()
        $script:WorkerBridge = $null
    }
})
$script:Window.Add_Closed({
    $dispatcher = [System.Windows.Threading.Dispatcher]::CurrentDispatcher
    if (-not $dispatcher.HasShutdownStarted) { $dispatcher.InvokeShutdown() }
})
$script:Window.Show()

if (-not $script:PreviewMode) {
    $windowHandle = (New-Object System.Windows.Interop.WindowInteropHelper($script:Window)).Handle
    Start-Worker $windowHandle
}

[System.Windows.Threading.Dispatcher]::Run()
