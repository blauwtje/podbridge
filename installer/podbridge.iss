#ifndef AppVersion
  #error Pass the version with ISCC /DAppVersion=<version>
#endif

[Setup]
AppId={{1D046C64-8CBA-456B-B0D9-752F8A4D01FF}
AppName=podbridge
AppVersion={#AppVersion}
AppPublisher=podbridge
DefaultDirName={autopf}\podbridge
DefaultGroupName=podbridge
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=podbridge-setup
UninstallDisplayIcon={app}\podbridge.exe
Compression=lzma2
SolidCompression=yes

[CustomMessages]
VbCableNotice=podbridge sends the AirPods mic to Discord through VB-CABLE, the Virtual Audio Cable by VB-Audio Software.%n%nVB-CABLE is donationware: it is VB-Audio's software, not part of podbridge, and is bundled unmodified as VBCABLE_Driver_Pack45.zip. Its origin is www.vb-cable.com, where you can support VB-Audio with a donation.%n%nSetup installs VB-CABLE only when no "CABLE Input" device exists yet, and then asks you to restart Windows.

[Files]
Source: "..\dist\podbridge\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\firmware\*.bin"; DestDir: "{app}\firmware"; Flags: ignoreversion
Source: "..\dist\firmware\LICENCE.rtlwifi_firmware.txt"; DestDir: "{app}\firmware"; Flags: ignoreversion
Source: "..\dist\tools\wdi-simple.exe"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "..\dist\vbcable\VBCABLE_Driver_Pack45.zip"; DestDir: "{app}\vbcable"; Flags: ignoreversion
Source: "..\dist\webview2\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
Source: "..\dist\vbcable\pack\*"; DestDir: "{app}\vbcable\pack"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\podbridge"; Filename: "{app}\podbridge.exe"; Parameters: "run"

[InstallDelete]
Type: files; Name: "{group}\podbridge doctor.lnk"
Type: files; Name: "{group}\podbridge restore.lnk"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\usb_driver"

[Run]
Filename: "{app}\vbcable\pack\VBCABLE_Setup_x64.exe"; Parameters: "-i -h"; Check: NeedsVbCable; StatusMsg: "Installing VB-CABLE..."; Flags: waituntilterminated
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; Check: WebView2Missing; StatusMsg: "Installing Microsoft Edge WebView2..."; Flags: waituntilterminated
Filename: "{app}\podbridge.exe"; Parameters: "run"; Description: "Start podbridge now"; Flags: postinstall nowait skipifsilent
Filename: "{app}\podbridge.exe"; Parameters: "run"; Flags: nowait skipifnotsilent

[UninstallRun]
Filename: "{app}\podbridge-cli.exe"; Parameters: "restore --remove-cert"; Flags: runhidden waituntilterminated; RunOnceId: "PodbridgeRestore"

[Code]
const
  RenderKey = 'SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render';
  DeviceDescValue = '{a45c254e-df1c-4efd-8020-67d146a850e0},2';
  DeviceStateNotPresent = 4;

  WebView2Key = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

var
  CableMissing: Boolean;

function EndpointIsCableInput(const Endpoint: String): Boolean;
var
  Name: String;
  State: Cardinal;
begin
  Result := False;
  if not RegQueryStringValue(HKLM64, RenderKey + '\' + Endpoint + '\Properties', DeviceDescValue, Name) then
    Exit;
  if not RegQueryDWordValue(HKLM64, RenderKey + '\' + Endpoint, 'DeviceState', State) then
    Exit;
  Result := (Pos('CABLE Input', Name) > 0) and ((State and DeviceStateNotPresent) = 0);
end;

function CableInputPresent: Boolean;
var
  Endpoints: TArrayOfString;
  I: Integer;
begin
  Result := False;
  if not RegGetSubkeyNames(HKLM64, RenderKey, Endpoints) then
    Exit;
  for I := 0 to GetArrayLength(Endpoints) - 1 do
    Result := Result or EndpointIsCableInput(Endpoints[I]);
end;

function InitializeSetup: Boolean;
begin
  CableMissing := not CableInputPresent;
  Result := True;
end;

procedure InitializeWizard;
begin
  CreateOutputMsgPage(wpLicense, 'VB-CABLE', 'VB-CABLE Virtual Audio Device by VB-Audio Software', CustomMessage('VbCableNotice'));
end;

function NeedsVbCable: Boolean;
begin
  Result := CableMissing;
end;

function WebView2Missing: Boolean;
var
  Version: String;
begin
  Result := not (RegQueryStringValue(HKLM64, WebView2Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

function NeedRestart: Boolean;
begin
  Result := CableMissing;
end;
