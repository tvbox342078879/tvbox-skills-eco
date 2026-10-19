param(
  [Parameter(Mandatory = $true, Position = 0)][string]$Share,
  [string]$OutDir = "",
  [ValidateSet('compat', 'best')][string]$Quality = 'compat',
  [switch]$MetadataOnly,
  [string]$Proxy = ""
)
# Download the platform original of a post, without the watermark the platform
# adds to the copy it hands out.
#
# How it works: the platform keeps a clean original and hands out a derived copy
# with its watermark drawn in.
#   Douyin video:           the playback copy has no watermark; the "download"
#                           copy is re-encoded with a logo, the author ID and an
#                           end card. This script saves the playback copy.
#   Xiaohongshu image note: the note page shows resized images with the platform
#                           mark drawn in; the original upload is stored under a
#                           file id. This script saves the stored original.
# It never edits pixels, never logs in, never stores cookies.
#
# Windows PowerShell 5.1. Needs only the curl.exe that ships with Windows 10/11.
# ASCII only on purpose: PowerShell 5.1 reads a UTF-8 file without BOM in the
# ANSI codepage and corrupts non-ASCII text. Chinese names are built from code
# points below.
#
# Output: one JSON object on stdout. status "ok" or "error"; errors carry a code
# the skill maps to a plain explanation.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File get-original.ps1 "<share text or link>"
#   ... -Quality best        highest resolution available (may be H.265)
#   ... -MetadataOnly        resolve and report, download nothing
#   ... -OutDir "D:\clips"   library root (default: Desktop\<library name>)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}

function U([int[]]$cp) { return (-join ($cp | ForEach-Object { [char]$_ })) }
$NAME_LIB     = U 0x53BB, 0x6C34, 0x5370, 0x539F, 0x7247   # library folder name
$NAME_DOUYIN  = U 0x6296, 0x97F3                           # file name prefix
$NAME_XHS     = U 0x5C0F, 0x7EA2, 0x4E66                   # file name prefix
$NAME_NOAUTH  = U 0x672A, 0x77E5, 0x4F5C, 0x8005           # author unknown
$NAME_NOTITLE = U 0x672A, 0x547D, 0x540D                   # untitled

$UA_MOBILE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
$UA_AWEME  = 'Aweme 350101 rv:350101 (iPhone; iOS 17.0; zh_CN) Cronet'
$DY_FEED   = 'https://aweme.snssdk.com/aweme/v1/feed/?aweme_id={0}&aid=1128'
$DY_HOSTS  = @('douyin.com', 'iesdouyin.com')
$XHS_HOSTS = @('xiaohongshu.com', 'xhslink.com', 'xhslink.cn')
# Xiaohongshu keeps each uploaded image by a file id. The note page shows a
# resized copy with the platform watermark drawn in; these hosts serve the
# stored original for the same file id, without it.
$XHS_IMAGE_HOSTS = @('https://sns-img-bd.xhscdn.com/', 'https://ci.xiaohongshu.com/', 'https://sns-img-qc.xhscdn.com/')
# Any of these in a media URL marks the watermarked copy. Never download those.
$WATERMARK_MARKERS = @('watermark=1', '/logo/', 'logo_name=', 'logo_type=')

$TEMP_DIR = Join-Path ([IO.Path]::GetTempPath()) ('qsy-' + [guid]::NewGuid().ToString('N').Substring(0, 10))

# ---------------------------------------------------------------- output ----

function Emit($obj) {
  Write-Output ($obj | ConvertTo-Json -Depth 6)
}

function Fail([string]$code, [string]$message) {
  throw ('QSY|' + $code + '|' + $message)
}

# ---------------------------------------------------------------- helpers ---

function Test-HostIn([string]$hostName, [string[]]$suffixes) {
  if (-not $hostName) { return $false }
  $h = $hostName.ToLowerInvariant().TrimEnd('.')
  foreach ($s in $suffixes) { if ($h -eq $s -or $h.EndsWith('.' + $s)) { return $true } }
  return $false
}

function Get-FirstLink([string]$text) {
  # Share text is usually "some words https://v.douyin.com/xxxx/ copy and open".
  # Only ASCII URL characters count, so Chinese text glued to the link is not
  # swallowed into it.
  $found = [regex]::Matches($text, "https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")
  foreach ($m in $found) {
    $u = $m.Value.TrimEnd(')', ']', '}', '.', ',', ';', '!', '?', "'")
    try { $parsed = [uri]$u } catch { continue }
    $platform = $null
    if (Test-HostIn $parsed.Host $DY_HOSTS) { $platform = 'douyin' }
    elseif (Test-HostIn $parsed.Host $XHS_HOSTS) { $platform = 'xiaohongshu' }
    if ($platform) {
      # Official hosts all serve https; upgrade a pasted http link instead of failing.
      if ($parsed.Scheme -eq 'http') { $u = 'https' + $u.Substring(4) }
      return @{ url = $u; platform = $platform }
    }
  }
  return $null
}

function Test-SafeMediaUrl([string]$url) {
  try { $u = [uri]$url } catch { return $false }
  if ($u.Scheme -ne 'https' -or -not $u.Host) { return $false }
  $h = $u.Host.ToLowerInvariant()
  if ($h -eq 'localhost' -or $h.EndsWith('.local')) { return $false }
  $ip = $null
  if ([System.Net.IPAddress]::TryParse($h, [ref]$ip)) { return $false }   # media never comes from a bare IP
  foreach ($mark in $WATERMARK_MARKERS) { if ($url.ToLowerInvariant().Contains($mark)) { return $false } }
  return $true
}

function Get-SafeName([string]$value, [string]$fallback, [int]$limit) {
  if (-not $value) { return $fallback }
  $value = $value.Normalize([Text.NormalizationForm]::FormKC)
  $sb = New-Object System.Text.StringBuilder
  foreach ($ch in $value.ToCharArray()) {
    $cat = [char]::GetUnicodeCategory($ch)
    if ('\/:*?"<>|'.IndexOf($ch) -ge 0) { continue }
    if ($cat -eq 'Control' -or $cat -eq 'Format' -or $cat -eq 'Surrogate' -or $cat -eq 'OtherSymbol' -or $cat -eq 'PrivateUse') { continue }
    [void]$sb.Append($ch)
  }
  $clean = ($sb.ToString() -replace '\s+', ' ').Trim(' ', '.', '-', '_', '#')
  if ($clean.Length -gt $limit) { $clean = $clean.Substring(0, $limit).TrimEnd(' ', '.') }
  $reserved = @('CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9', 'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9')
  if (-not $clean -or $reserved -contains $clean.ToUpperInvariant()) { return $fallback }
  return $clean
}

# ---------------------------------------------------------------- curl ------

$CURL = $null
$cmd = Get-Command curl.exe -ErrorAction SilentlyContinue
if ($cmd) { $CURL = $cmd.Source }

function Invoke-Curl {
  param([string]$Url, [string]$OutFile, [string]$UA, [string]$Referer = '', [int]$MaxTime = 60)
  $errFile = Join-Path $TEMP_DIR ('curl-' + [guid]::NewGuid().ToString('N').Substring(0, 6) + '.err')
  $a = @('--location', '--silent', '--show-error', '--compressed',
         '--proto', '=https', '--proto-redir', '=https',
         '--connect-timeout', '10', '--max-time', "$MaxTime", '--retry', '2',
         '--user-agent', $UA, '--stderr', $errFile,
         '--write-out', '%{http_code} %{url_effective}')
  if ($Referer) { $a += @('--referer', $Referer) }
  if ($Proxy) { $a += @('--proxy', $Proxy) }
  if ($OutFile) { $a += @('--output', $OutFile) } else { $a += @('--output', 'NUL') }
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  $wo = & $CURL @($a + $Url)
  $rc = $LASTEXITCODE
  $err = ''
  if (Test-Path $errFile) { $err = ([IO.File]::ReadAllText($errFile)).Trim() }
  # Windows curl (schannel) fails the whole handshake when it cannot reach the
  # certificate revocation server, which happens for some hosts on some
  # networks. Retry that one case without the revocation lookup; the
  # certificate itself is still verified.
  if ($rc -eq 35 -and $err -match 'CRYPT_E_REVOCATION|CRYPT_E_NO_REVOCATION|0x80092012|0x80092013') {
    $wo = & $CURL @($a + '--ssl-no-revoke' + $Url)
    $rc = $LASTEXITCODE
    $err = ''
    if (Test-Path $errFile) { $err = ([IO.File]::ReadAllText($errFile)).Trim() }
  }
  $ErrorActionPreference = $prev
  $code = 0; $final = $Url
  if ($wo) {
    $parts = "$wo".Trim().Split(' ', 2)
    [void][int]::TryParse($parts[0], [ref]$code)
    if ($parts.Count -gt 1) { $final = $parts[1] }
  }
  return [ordered]@{ exit = $rc; status = $code; final = $final; error = $err }
}

# ---------------------------------------------------------------- douyin ----

function Get-DouyinId([string]$url) {
  $u = [uri]$url
  $q = [System.Web.HttpUtility]::ParseQueryString($u.Query)
  if ($q['modal_id'] -and $q['modal_id'] -match '^\d{15,22}$') { return @{ id = $q['modal_id']; kind = 'video' } }
  $m = [regex]::Match($u.AbsolutePath, '/(video|note|slides)/(\d{15,22})')
  if ($m.Success) { return @{ id = $m.Groups[2].Value; kind = $m.Groups[1].Value } }
  return $null
}

function Get-DouyinItem([string]$id) {
  # The feed endpoint returns a small recommendation list seeded by the id.
  # The requested post is in it only when it is public and alive, so match the
  # id exactly and never fall back to another item.
  for ($try = 1; $try -le 3; $try++) {
    $f = Join-Path $TEMP_DIR ("feed-$try.json")
    $r = Invoke-Curl -Url ($DY_FEED -f $id) -OutFile $f -UA $UA_AWEME -MaxTime 30
    if ($r.exit -ne 0 -or $r.status -ne 200 -or -not (Test-Path $f)) { continue }
    try { $d = [IO.File]::ReadAllText($f, [Text.Encoding]::UTF8) | ConvertFrom-Json } catch { continue }
    foreach ($a in @($d.aweme_list)) {
      if ("$($a.aweme_id)" -eq $id) { return $a }
    }
  }
  return $null
}

function Get-DouyinSources($item) {
  $v = $item.video
  $list = New-Object System.Collections.ArrayList
  $seen = @{}
  $add = {
    param($name, $node, $codec, $bitrate)
    if (-not $node) { return }
    foreach ($url in @($node.url_list)) {
      if (-not $url -or $seen.ContainsKey($url)) { continue }
      if (-not (Test-SafeMediaUrl $url)) { continue }
      $seen[$url] = $true
      [void]$list.Add([ordered]@{
        source = $name; url = $url; codec = $codec
        width = [int]("0" + $node.width); height = [int]("0" + $node.height)
        bitrate = [int64]("0" + $bitrate); bytes = [int64]("0" + $node.data_size)
      })
    }
  }
  if ($v) {
    & $add 'play_addr_h264' $v.play_addr_h264 'h264' 0
    $pc = 'h264'
    if ("$($v.play_addr.url_key)" -match 'bytevc1|h265') { $pc = 'h265' }
    & $add 'play_addr' $v.play_addr $pc 0
    foreach ($r in @($v.bit_rate)) {
      if (-not $r) { continue }
      $c = 'h264'
      if ("$($r.is_h265)" -eq '1' -or "$($r.play_addr.url_key)" -match 'bytevc1|h265') { $c = 'h265' }
      & $add ('bit_rate:' + $r.gear_name) $r.play_addr $c $r.bit_rate
    }
    # download_addr is deliberately never read: it is the watermarked copy.
  }
  return , $list
}

function Select-Order($sources, [string]$quality) {
  # compat: H.264 first (plays everywhere), then height, then bitrate.
  # best:   height first, H.264 preferred on a tie, then bitrate.
  if ($quality -eq 'best') {
    return @($sources | Sort-Object @{ e = { $_.height }; Descending = $true }, @{ e = { if ($_.codec -eq 'h264') { 1 } else { 0 } }; Descending = $true }, @{ e = { $_.bitrate }; Descending = $true })
  }
  return @($sources | Sort-Object @{ e = { if ($_.codec -eq 'h264') { 1 } else { 0 } }; Descending = $true }, @{ e = { $_.height }; Descending = $true }, @{ e = { $_.bitrate }; Descending = $true })
}

function Test-Mp4([string]$path) {
  $fs = [IO.File]::OpenRead($path)
  try {
    $buf = New-Object byte[] 12
    $n = $fs.Read($buf, 0, 12)
    if ($n -lt 12) { return $false }
    return ([Text.Encoding]::ASCII.GetString($buf, 4, 4) -eq 'ftyp')
  } finally { $fs.Close() }
}

function Get-AuthorDir([string]$root, [string]$author) {
  $rootFull = [IO.Path]::GetFullPath($root)
  if (-not (Test-Path $rootFull)) { New-Item -ItemType Directory -Force -Path $rootFull | Out-Null }
  $dir = Join-Path $rootFull (Get-SafeName $author $NAME_NOAUTH 48)
  if (Test-Path $dir) {
    $it = Get-Item -LiteralPath $dir -Force
    if ($it.Attributes -band [IO.FileAttributes]::ReparsePoint) { Fail 'UNSAFE_PATH' 'author folder is a link; stopped to avoid writing outside the library' }
  } else {
    New-Item -ItemType Directory -Path $dir | Out-Null
  }
  if ([IO.Path]::GetFullPath((Split-Path $dir -Parent)) -ne $rootFull.TrimEnd('\')) { Fail 'UNSAFE_PATH' 'author folder resolved outside the library' }
  return $dir
}

function Get-UniquePath([string]$dir, [string]$base, [string]$ext) {
  $p = Join-Path $dir ($base + $ext)
  $n = 2
  while (Test-Path -LiteralPath $p) { $p = Join-Path $dir ($base + '-' + $n + $ext); $n++ }
  return $p
}

function Save-Douyin([string]$link) {
  $r = Invoke-Curl -Url $link -OutFile '' -UA $UA_MOBILE -MaxTime 25
  if ($r.exit -ne 0 -or $r.status -eq 0) { Fail 'NETWORK' ('could not open the share link: ' + $r.error) }
  $final = $r.final
  try { $fu = [uri]$final } catch { Fail 'BAD_REDIRECT' 'share link redirected to an unreadable address' }
  if (-not (Test-HostIn $fu.Host $DY_HOSTS)) { Fail 'BAD_REDIRECT' ('share link left the official domain: ' + $fu.Host) }

  $idInfo = Get-DouyinId $final
  if (-not $idInfo) { $idInfo = Get-DouyinId $link }
  if (-not $idInfo) { Fail 'NO_ID' 'opened the link but found no post id' }
  if ($idInfo.kind -ne 'video') { Fail 'IMAGE_POST' 'Douyin image posts are not supported in this version' }
  $id = $idInfo.id

  $item = Get-DouyinItem $id
  if (-not $item) { Fail 'NOT_FOUND' 'the platform did not return this post (deleted, private, login-only, or rate limited)' }
  if (@($item.images).Count -gt 0 -and -not $item.video) { Fail 'IMAGE_POST' 'Douyin image posts are not supported in this version' }

  $sources = Get-DouyinSources $item
  if ($sources.Count -eq 0) { Fail 'NO_CLEAN_SOURCE' 'post found, but it carries no playback copy without a watermark' }
  $ordered = Select-Order $sources $Quality
  $title = "$($item.desc)"
  $author = "$($item.author.nickname)"
  $durationS = $null
  if ($item.video.duration) { $durationS = [math]::Round([double]$item.video.duration / 1000, 2) }
  $top = $ordered[0]

  if ($MetadataOnly) {
    return [ordered]@{
      status = 'ok'; mode = 'metadata_only'; platform = 'douyin'; kind = 'video'; id = $id
      title = $title; author = $author; duration_seconds = $durationS
      would_save = [ordered]@{ source = $top.source; codec = $top.codec; width = $top.width; height = $top.height }
      clean_sources = $sources.Count
    }
  }

  $root = $OutDir
  if (-not $root) { $root = Join-Path ([Environment]::GetFolderPath('Desktop')) $NAME_LIB }
  $dir = Get-AuthorDir $root $author
  $base = $NAME_DOUYIN + '-' + (Get-SafeName $title $NAME_NOTITLE 50) + '-' + $id
  $dest = Get-UniquePath $dir $base '.mp4'
  $part = $dest + '.part'

  $errors = @()
  $picked = $null
  foreach ($s in $ordered) {
    if (Test-Path -LiteralPath $part) { [IO.File]::Delete($part) }
    $dl = Invoke-Curl -Url $s.url -OutFile $part -UA $UA_MOBILE -Referer 'https://www.douyin.com/' -MaxTime 1800
    $ok = ($dl.exit -eq 0 -and ($dl.status -eq 200 -or $dl.status -eq 206) -and (Test-Path -LiteralPath $part))
    if ($ok) {
      $len = (Get-Item -LiteralPath $part).Length
      if ($len -gt 10240 -and (Test-Mp4 $part)) { $picked = $s; break }
      $errors += ("{0}: not an mp4 ({1} bytes)" -f $s.source, $len)
    } else {
      $errors += ("{0}: http {1} {2}" -f $s.source, $dl.status, $dl.error)
    }
  }
  if (-not $picked) {
    if (Test-Path -LiteralPath $part) { [IO.File]::Delete($part) }
    Fail 'DOWNLOAD_FAILED' ('every clean source failed: ' + (($errors | Select-Object -Last 3) -join ' | '))
  }
  [IO.File]::Move($part, $dest)
  $fi = Get-Item -LiteralPath $dest

  return [ordered]@{
    status = 'ok'; platform = 'douyin'; kind = 'video'; id = $id
    title = $title; author = $author; duration_seconds = $durationS
    library = [IO.Path]::GetFullPath($root); folder = $dir
    files = @([ordered]@{
      path = $fi.FullName; bytes = $fi.Length
      source = $picked.source; codec = $picked.codec; width = $picked.width; height = $picked.height
    })
    transcoded = $false
    higher_resolution_available = [bool](@($sources | Where-Object { $_.height -gt $picked.height }).Count -gt 0)
  }
}

# ---------------------------------------------------------------- xiaohongshu

function Get-ImageExt([string]$path) {
  $fs = [IO.File]::OpenRead($path)
  try {
    $b = New-Object byte[] 16
    $n = $fs.Read($b, 0, 16)
  } finally { $fs.Close() }
  if ($n -lt 12) { return $null }
  if ($b[0] -eq 0xFF -and $b[1] -eq 0xD8 -and $b[2] -eq 0xFF) { return '.jpg' }
  if ($b[0] -eq 0x89 -and $b[1] -eq 0x50 -and $b[2] -eq 0x4E -and $b[3] -eq 0x47) { return '.png' }
  $s4 = [Text.Encoding]::ASCII.GetString($b, 0, 4)
  $s8 = [Text.Encoding]::ASCII.GetString($b, 8, 4)
  if ($s4 -eq 'RIFF' -and $s8 -eq 'WEBP') { return '.webp' }
  $brand = [Text.Encoding]::ASCII.GetString($b, 4, 8)
  if ($brand -match '^ftyp(heic|heix|mif1|hevc)') { return '.heic' }
  if ($brand -match '^ftypavi') { return '.avif' }
  return $null
}

function Get-XhsNote($state, [string]$noteId) {
  # Mobile share pages carry the note under noteData; the desktop layout uses
  # note.noteDetailMap. Accept either, and only the note that was asked for.
  $n = $null
  try { $n = $state.noteData.data.noteData } catch {}
  if ($n -and "$($n.noteId)" -eq $noteId) { return $n }
  try {
    $w = $state.note.noteDetailMap.$noteId
    if ($w -and $w.note) { return $w.note }
  } catch {}
  return $null
}

function Save-Xhs([string]$link) {
  $page = Join-Path $TEMP_DIR 'note.html'
  $r = Invoke-Curl -Url $link -OutFile $page -UA $UA_MOBILE -MaxTime 30
  if ($r.exit -ne 0 -or $r.status -eq 0) { Fail 'NETWORK' ('could not open the share link: ' + $r.error) }
  try { $fu = [uri]$r.final } catch { Fail 'BAD_REDIRECT' 'share link redirected to an unreadable address' }
  if (-not (Test-HostIn $fu.Host $XHS_HOSTS)) { Fail 'BAD_REDIRECT' ('share link left the official domain: ' + $fu.Host) }
  # An expired link, a note the owner hid, or a link without its token lands on
  # the home page, the login page or the security 404 page.
  $m = [regex]::Match($fu.AbsolutePath, '/(?:discovery/item|explore)/([0-9a-f]{24})')
  if (-not $m.Success) { Fail 'LINK_EXPIRED' 'the link no longer opens a note (expired, hidden, or copied without its token)' }
  $noteId = $m.Groups[1].Value

  $html = [IO.File]::ReadAllText($page, [Text.Encoding]::UTF8)
  $sm = [regex]::Match($html, 'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>', 'Singleline')
  if (-not $sm.Success) { Fail 'LINK_EXPIRED' 'the note page carried no note data' }
  $js = [regex]::Replace($sm.Groups[1].Value, '(?<=[:\[,])undefined(?=[,\]\}])', 'null')
  try { $state = $js | ConvertFrom-Json } catch { Fail 'NOT_FOUND' 'the note page data could not be read' }
  $note = Get-XhsNote $state $noteId
  if (-not $note) { Fail 'LINK_EXPIRED' 'the note page did not include this note (expired link or hidden note)' }

  $type = "$($note.type)"
  $title = "$($note.title)"
  if (-not $title) { $title = "$($note.desc)" }
  $author = "$($note.user.nickName)"
  if (-not $author) { $author = "$($note.user.nickname)" }
  if ($type -eq 'video') { Fail 'XHS_VIDEO' 'Xiaohongshu video notes are not supported in this version yet' }

  $images = @($note.imageList | Where-Object { $_ -and "$($_.fileId)" })
  if ($images.Count -eq 0) { Fail 'NO_CLEAN_SOURCE' 'note found, but it carries no original image ids' }
  $livePhotos = @($images | Where-Object { "$($_.livePhoto)" -eq 'True' }).Count

  if ($MetadataOnly) {
    return [ordered]@{
      status = 'ok'; mode = 'metadata_only'; platform = 'xiaohongshu'; kind = 'images'; id = $noteId
      title = $title; author = $author; images = $images.Count; live_photos = $livePhotos
    }
  }

  $root = $OutDir
  if (-not $root) { $root = Join-Path ([Environment]::GetFolderPath('Desktop')) $NAME_LIB }
  $authorDir = Get-AuthorDir $root $author
  $noteDir = Get-UniquePath $authorDir ($NAME_XHS + '-' + (Get-SafeName $title $NAME_NOTITLE 40) + '-' + $noteId) ''
  New-Item -ItemType Directory -Path $noteDir | Out-Null

  $files = @()
  $failed = @()
  $i = 0
  foreach ($im in $images) {
    $i++
    $num = $i.ToString('00')
    $part = Join-Path $noteDir ($num + '.part')
    $saved = $null
    foreach ($h in $XHS_IMAGE_HOSTS) {
      $url = $h + $im.fileId
      if (-not (Test-SafeMediaUrl $url)) { continue }
      if (Test-Path -LiteralPath $part) { [IO.File]::Delete($part) }
      $dl = Invoke-Curl -Url $url -OutFile $part -UA $UA_MOBILE -Referer 'https://www.xiaohongshu.com/' -MaxTime 120
      if ($dl.exit -eq 0 -and $dl.status -eq 200 -and (Test-Path -LiteralPath $part) -and (Get-Item -LiteralPath $part).Length -gt 1024) {
        $ext = Get-ImageExt $part
        if ($ext) {
          $dest = Join-Path $noteDir ($num + $ext)
          [IO.File]::Move($part, $dest)
          $fi = Get-Item -LiteralPath $dest
          $saved = [ordered]@{ path = $fi.FullName; bytes = $fi.Length; format = $ext.TrimStart('.') }
          break
        }
      }
    }
    if (Test-Path -LiteralPath $part) { [IO.File]::Delete($part) }
    # The watermarked page copy is never used as a fallback.
    if ($saved) { $files += $saved } else { $failed += $i }
  }
  if ($files.Count -eq 0) {
    [IO.Directory]::Delete($noteDir, $true)
    Fail 'DOWNLOAD_FAILED' 'no original image could be downloaded'
  }

  return [ordered]@{
    status = 'ok'; platform = 'xiaohongshu'; kind = 'images'; id = $noteId
    title = $title; author = $author
    library = [IO.Path]::GetFullPath($root); folder = $noteDir
    files = $files
    failed_images = $failed
    live_photos_saved_as_still = $livePhotos
    transcoded = $false
  }
}

# ---------------------------------------------------------------- main ------

try {
  Add-Type -AssemblyName System.Web
  if (-not $CURL) { Fail 'NO_CURL' 'curl.exe was not found; Windows 10 1803 or later ships it in System32' }
  New-Item -ItemType Directory -Force -Path $TEMP_DIR | Out-Null
  $link = Get-FirstLink $Share
  if (-not $link) { Fail 'NO_LINK' 'no official Douyin or Xiaohongshu link found in the input' }
  if ($link.platform -eq 'xiaohongshu') { $result = Save-Xhs $link.url }
  else { $result = Save-Douyin $link.url }
  Emit $result
  exit 0
} catch {
  $msg = "$($_.Exception.Message)"
  if ($msg.StartsWith('QSY|')) {
    $p = $msg.Split('|', 3)
    Emit ([ordered]@{ status = 'error'; code = $p[1]; message = $p[2] })
  } else {
    Emit ([ordered]@{ status = 'error'; code = 'UNEXPECTED'; message = $msg })
  }
  exit 1
} finally {
  if (Test-Path $TEMP_DIR) { [IO.Directory]::Delete($TEMP_DIR, $true) }
}
