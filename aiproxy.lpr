{$mode objfpc}{$H+}
{$APPTYPE CONSOLE}

program AIProxy;

uses
  Classes,
  SysUtils,
  IniFiles,
  fphttpclient,
  openssl,
  Math,
  Windows,
  fpjson;

const
  PROGRAM_VERSION = 'v1.8';
  DEFAULT_CONFIG = 'AIProxy.ini';
  DEFAULT_LM_URL = 'http://localhost:1234/v1/chat/completions';
  DEFAULT_MODEL = 'meta-llama-3-8b-instruct';
  DEFAULT_PORT = 8080;
  DEFAULT_DEBUG_LINES = 10;
  DEFAULT_MAX_TOKENS = -1;
  RENDER_THROTTLE_MS = 100; // 10Hz smooth dashboard ticker tracking updates

  DEFAULT_ANSI_HEADER = #27'[1;36m';
  DEFAULT_ANSI_STATS = #27'[1;35m';
  DEFAULT_ANSI_INPUT = #27'[0;32m';
  DEFAULT_ANSI_OUTPUT = #27'[0;33m';
  DEFAULT_ANSI_RESET = #27'[0m';
  DEFAULT_ANSI_CLRLN = #27'[K';
  // ANSI Escape Sequence: Clear to end of current line

  ENABLE_VIRTUAL_TERMINAL_PROCESSING = $0004;

type
  TAIProxyConfig = class
  private
    FDefaultPrompt: string;
    FLMS_URL: string;
    FModelName: string;
    FPort: integer;
    FOutputFile: string;
    FApiKey: string;
    FAuthEnabled: boolean;
    FMaxTokens: integer;
    FAnsiHeader: string;
    FAnsiInput: string;
    FAnsiOutput: string;
    FAnsiStats: string;
  public
    constructor Create;
    procedure LoadFromINI(const FileName: string);
    property DefaultPrompt: string read FDefaultPrompt write FDefaultPrompt;
    property LM_URL: string read FLMS_URL write FLMS_URL;
    property ModelName: string read FModelName write FModelName;
    property Port: integer read FPort write FPort;
    property OutputFile: string read FOutputFile write FOutputFile;
    property ApiKey: string read FApiKey write FApiKey;
    property AuthEnabled: boolean read FAuthEnabled write FAuthEnabled;
    property MaxTokens: integer read FMaxTokens write FMaxTokens;
    property AnsiHeader: string read FAnsiHeader write FAnsiHeader;
    property AnsiInput: string read FAnsiInput write FAnsiInput;
    property AnsiOutput: string read FAnsiOutput write FAnsiOutput;
    property AnsiStats: string read FAnsiStats write FAnsiStats;
  end;

  TAIStreamingStream = class(TStream)
  public
    function Write(const Buffer; Count: longint): longint; override;
    function Read(var Buffer; Count: longint): longint; override;
    function Seek(Offset: longint; Origin: word): longint; override;
  end;

var
  GlobalConfig: TAIProxyConfig;
  DebugMode: boolean = False;
  QuietMode: boolean = False;
  CommandLinePrompt: string = '';
  CommandLineKey: string = '';
  CommandLineModel: string = '';
  CommandLineAuthToggle: string = '';
  CommandLineInFile: string = '';
  CommandLineOutFile: string = '';
  CommandLineMaxTokens: integer = DEFAULT_MAX_TOKENS;
  MaxHistoryLines: integer = DEFAULT_DEBUG_LINES;

  InputHistoryList: TStringList;
  OutputHistoryList: TStringList;
  StartTimeTick: DWORD;
  TotalBytesIn: int64 = 0;
  TotalBytesOut: int64 = 0;
  LastRenderTick: DWORD = 0;
  StreamBufferRemainder: string = '';
  FullAssembledOutput: string = '';

  function ExtractStreamingDeltaContent(const SSELine: string): string; forward;
  procedure RenderDebugConsoleDisplay(ForceRender: boolean); forward;

  { TAIProxyConfig }

  constructor TAIProxyConfig.Create;
  begin
    inherited Create;
    FDefaultPrompt := '';
    FLMS_URL := DEFAULT_LM_URL;
    FModelName := DEFAULT_MODEL;
    FPort := DEFAULT_PORT;
    FOutputFile := '';
    FApiKey := '';
    FAuthEnabled := False;
    FMaxTokens := DEFAULT_MAX_TOKENS;
    FAnsiHeader := DEFAULT_ANSI_HEADER;
    FAnsiInput := DEFAULT_ANSI_INPUT;
    FAnsiOutput := DEFAULT_ANSI_OUTPUT;
    FAnsiStats := DEFAULT_ANSI_STATS;
  end;

  function ResolveAnsiEscapeCodes(const ConfigStr: string; const Fallback: string): string;
  begin
    if Trim(ConfigStr) = '' then
    begin
      Result := Fallback;
      Exit;
    end;
    Result := StringReplace(ConfigStr, '\e', #27, [rfReplaceAll, rfIgnoreCase]);
    Result := StringReplace(Result, '\033', #27, [rfReplaceAll]);
  end;

  procedure TAIProxyConfig.LoadFromINI(const FileName: string);
  var
    Ini: TIniFile;
    Section: string;
  begin
    if not FileExists(FileName) then Exit;
    Ini := TIniFile.Create(FileName);
    try
      Section := 'LMStudio';
      FLMS_URL := Ini.ReadString(Section, 'URL', DEFAULT_LM_URL);
      FModelName := Ini.ReadString(Section, 'Model', DEFAULT_MODEL);
      FPort := Ini.ReadInteger(Section, 'Port', DEFAULT_PORT);
      FApiKey := Ini.ReadString(Section, 'ApiKey', '');
      FAuthEnabled := Ini.ReadBool(Section, 'AuthEnabled', False);
      FMaxTokens := Ini.ReadInteger(Section, 'MaxTokens', DEFAULT_MAX_TOKENS);

      Section := 'Output';
      FOutputFile := Ini.ReadString(Section, 'FileName', '');

      Section := 'Colors';
      FAnsiHeader := ResolveAnsiEscapeCodes(Ini.ReadString(Section, 'HeaderColor', ''),
        DEFAULT_ANSI_HEADER);
      FAnsiInput := ResolveAnsiEscapeCodes(Ini.ReadString(Section,
        'InputMarkerColor', ''), DEFAULT_ANSI_INPUT);
      FAnsiOutput := ResolveAnsiEscapeCodes(Ini.ReadString(Section,
        'OutputMarkerColor', ''), DEFAULT_ANSI_OUTPUT);
      FAnsiStats := ResolveAnsiEscapeCodes(Ini.ReadString(Section, 'StatsColor', ''),
        DEFAULT_ANSI_STATS);
    finally
      Ini.Free;
    end;
  end;

  function ExtractStreamingDeltaContent(const SSELine: string): string;
  var
    StartPos, EndPos: integer;
    Token: string;
    i: integer;
  begin
    Result := '';
    Token := '"delta":{"content":';

    StartPos := Pos(Token, SSELine);
    if StartPos = 0 then Exit;

    Inc(StartPos, Length(Token));

    while (StartPos <= Length(SSELine)) and (SSELine[StartPos] in [' ', #9, '"']) do
      Inc(StartPos);

    EndPos := StartPos;
    while EndPos <= Length(SSELine) do
    begin
      if (SSELine[EndPos] = '"') and (SSELine[EndPos - 1] <> '\') then
        Break;
      Inc(EndPos);
    end;

    Token := Copy(SSELine, StartPos, EndPos - StartPos);

    i := 1;
    while i <= Length(Token) do
    begin
      if (Token[i] = '\') and (i < Length(Token)) then
      begin
        case Token[i + 1] of
          'n': Result := Result + LineEnding;
          'r': ;
          't': Result := Result + #9;
          '"': Result := Result + '"';
          '\': Result := Result + '\';
          else
            Result := Result + Token[i] + Token[i + 1];
        end;
        Inc(i, 2);
      end
      else
      begin
        Result := Result + Token[i];
        Inc(i);
      end;
    end;
  end;

  procedure AppendTokenToOutputHistory(const TokenText: string);
  var
    i: integer;
  begin
    if OutputHistoryList.Count = 0 then
      OutputHistoryList.Add('');

    for i := 1 to Length(TokenText) do
    begin
      if TokenText[i] = #10 then
        OutputHistoryList.Add('')
      else if TokenText[i] <> #13 then
        OutputHistoryList[OutputHistoryList.Count - 1] :=
          OutputHistoryList[OutputHistoryList.Count - 1] + TokenText[i];
    end;
  end;

  function TAIStreamingStream.Read(var Buffer; Count: longint): longint;
  begin
    Result := 0;
  end;

  function TAIStreamingStream.Seek(Offset: longint; Origin: word): longint;
  begin
    Result := 0;
  end;

  function TAIStreamingStream.Write(const Buffer; Count: longint): longint;
  var
    RawChunk: string;
    ResponseLines: TStringList;
    CurrentLine: string;
    DeltaText: string;
    i: integer;
  begin
    Result := Count;
    if Count <= 0 then Exit;

    TotalBytesIn := TotalBytesIn + Count;
    SetString(RawChunk, PChar(@Buffer), Count);
    StreamBufferRemainder := StreamBufferRemainder + RawChunk;

    ResponseLines := TStringList.Create;
    try
      ResponseLines.Text := StreamBufferRemainder;
      if ResponseLines.Count > 1 then
      begin
        for i := 0 to ResponseLines.Count - 2 do
        begin
          CurrentLine := Trim(ResponseLines[i]);
          if (CurrentLine = '') or (CurrentLine = 'data: [DONE]') then Continue;

          DeltaText := ExtractStreamingDeltaContent(CurrentLine);
          if DeltaText <> '' then
          begin
            FullAssembledOutput := FullAssembledOutput + DeltaText;
            if DebugMode then
            begin
              AppendTokenToOutputHistory(DeltaText);
              RenderDebugConsoleDisplay(False);
            end
            else
            begin
              System.Write(DeltaText);
              System.Flush(Output);
            end;
          end;
        end;
        StreamBufferRemainder := ResponseLines[ResponseLines.Count - 1];
      end;
    finally
      ResponseLines.Free;
    end;
  end;

  procedure SafeClearScreen;
  var
    StdoutHandle: THandle;
    ScreenInfo: TConsoleScreenBufferInfo;
    CharsWritten: DWORD;
    ConsoleSize: DWORD;
    TargetCoord: COORD;
  begin
    StdoutHandle := GetStdHandle(STD_OUTPUT_HANDLE);
    if StdoutHandle = INVALID_HANDLE_VALUE then Exit;

    if GetConsoleScreenBufferInfo(StdoutHandle, ScreenInfo) then
    begin
      ConsoleSize := ScreenInfo.dwSize.X * ScreenInfo.dwSize.Y;
      TargetCoord.X := 0;
      TargetCoord.Y := 0;
      FillConsoleOutputCharacter(StdoutHandle, ' ', ConsoleSize, TargetCoord,
        CharsWritten);
      FillConsoleOutputAttribute(StdoutHandle, ScreenInfo.wAttributes,
        ConsoleSize, TargetCoord, CharsWritten);
      SetConsoleCursorPosition(StdoutHandle, TargetCoord);
    end;
  end;

  procedure ResetConsoleCursor;
  var
    StdoutHandle: THandle;
    TargetCoord: COORD;
  begin
    StdoutHandle := GetStdHandle(STD_OUTPUT_HANDLE);
    if StdoutHandle <> INVALID_HANDLE_VALUE then
    begin
      TargetCoord.X := 0;
      TargetCoord.Y := 0;
      SetConsoleCursorPosition(StdoutHandle, TargetCoord);
    end;
  end;

  procedure RenderDebugConsoleDisplay(ForceRender: boolean);
  var
    i, StartIdx: integer;
    HdrClr, InClr, OutClr, StatClr, ResetClr, ClrLn: string;
    CurrentTick: DWORD;
    ElapsedSecs: double;
    AuthStr, TokenStr: string;
  begin
    if not DebugMode then Exit;

    CurrentTick := GetTickCount;
    if not ForceRender then
    begin
      if (CurrentTick - LastRenderTick) < RENDER_THROTTLE_MS then Exit;
    end;
    LastRenderTick := CurrentTick;

    ResetConsoleCursor;

    HdrClr := GlobalConfig.AnsiHeader;
    InClr := GlobalConfig.AnsiInput;
    OutClr := GlobalConfig.AnsiOutput;
    StatClr := GlobalConfig.AnsiStats;
    ResetClr := DEFAULT_ANSI_RESET;
    ClrLn := DEFAULT_ANSI_CLRLN;

    ElapsedSecs := (CurrentTick - StartTimeTick) / 1000.0;


    if GlobalConfig.AuthEnabled then AuthStr := 'ON'
    else
      AuthStr := 'OFF';
    if GlobalConfig.MaxTokens = -1 then TokenStr := 'Default'
    else
      TokenStr := IntToStr(GlobalConfig.MaxTokens);
    // Condensed Top Summary Dashboard
    WriteLn(StatClr + '[ AIProxy ' + PROGRAM_VERSION + ' Dashboard ]' + ResetClr + ClrLn);
    WriteLn(StatClr + 'Time: ' + ResetClr + FloatToStrF(ElapsedSecs, ffFixed, 0, 2) +
      's | ' + StatClr + 'In: ' + ResetClr + IntToStr(TotalBytesIn) +
      'B | ' + StatClr + 'Out: ' + ResetClr + IntToStr(TotalBytesOut) + 'B' + ClrLn);
    WriteLn(StatClr + 'Model: ' + ResetClr + GlobalConfig.ModelName +
      ' | ' + StatClr + 'Auth: ' + ResetClr + AuthStr + ' | ' + StatClr +
      'MaxTokens: ' + ResetClr + TokenStr + ClrLn);
    WriteLn(ClrLn);
    // Tail -f Input Panel
    WriteLn(HdrClr +
      '=========================== INPUT HISTORY (TAIL -F) ==========================='
      + ResetClr + ClrLn);
    StartIdx := Max(0, InputHistoryList.Count - MaxHistoryLines);
    for i := StartIdx to InputHistoryList.Count - 1 do
      WriteLn(InClr + '>>> ' + ResetClr + InputHistoryList[i] + ClrLn);
    // FIX: Force clear obsolete trailing lines when scrolling shifts data up
    if (InputHistoryList.Count - StartIdx) < MaxHistoryLines then
      for i := 1 to MaxHistoryLines - (InputHistoryList.Count - StartIdx) do WriteLn(ClrLn);
    WriteLn(ClrLn);
    // Tail -f Output Panel
    WriteLn(HdrClr +
      '=========================== OUTPUT HISTORY (TAIL -F) =========================='
      + ResetClr + ClrLn);
    StartIdx := Max(0, OutputHistoryList.Count - MaxHistoryLines);
    for i := StartIdx to OutputHistoryList.Count - 1 do
      WriteLn(OutClr + '<<< ' + ResetClr + OutputHistoryList[i] + ClrLn);
    // FIX: Force clear obsolete trailing lines when scrolling shifts data up
    if (OutputHistoryList.Count - StartIdx) < MaxHistoryLines then
      for i := 1 to MaxHistoryLines - (OutputHistoryList.Count - StartIdx) do WriteLn(ClrLn);
    WriteLn(HdrClr +
      '================================================================================' +
      ResetClr + ClrLn);
    Flush(Output);
  end;

  procedure EnableWindowsAnsiFormatting;
  var
    StdoutHandle: THandle;
    ConsoleMode: DWORD;
  begin
    StdoutHandle := GetStdHandle(STD_OUTPUT_HANDLE);
    if StdoutHandle <> INVALID_HANDLE_VALUE then
    begin
      if GetConsoleMode(StdoutHandle, ConsoleMode) then
      begin
        ConsoleMode := ConsoleMode or ENABLE_VIRTUAL_TERMINAL_PROCESSING;
        SetConsoleMode(StdoutHandle, ConsoleMode);
      end;
    end;
  end;

  function GetConfigPath: string;
  begin
    Result := ExtractFilePath(ParamStr(0)) + DEFAULT_CONFIG;
  end;

  procedure ShowHelp;
  begin
    WriteLn('AIProxy - CLI Standard I/O Wrapper for Local Inference');
    WriteLn;
    WriteLn('Usage: | AIProxy [options] [-outfile output.txt]');
    Halt(0);
  end;

  procedure ParseCommandLine;
  var
    i: integer;
    ConfigPath: string;
    Param: string;
  begin
    ConfigPath := GetConfigPath;
    i := 1;
    while i <= ParamCount do
    begin
      Param := LowerCase(ParamStr(i));
      if (Param = '/config') or (Param = '-config') then
      begin
        Inc(i);
        if i <= ParamCount then ConfigPath := ParamStr(i);
      end
      else if (Param = '/prompt') or (Param = '-prompt') then
      begin
        Inc(i);
        if i <= ParamCount then CommandLinePrompt := ParamStr(i);
      end
      else if (Param = '/url') or (Param = '-url') then
      begin
        Inc(i);
        if i <= ParamCount then GlobalConfig.LM_URL := ParamStr(i);
      end
      else if (Param = '/model') or (Param = '-model') then
      begin
        Inc(i);
        if i <= ParamCount then CommandLineModel := ParamStr(i);
      end
      else if (Param = '/auth') or (Param = '-auth') then
      begin
        Inc(i);
        if i <= ParamCount then CommandLineAuthToggle := LowerCase(ParamStr(i));
      end
      else if (Param = '/key') or (Param = '-key') then
      begin
        Inc(i);
        if i <= ParamCount then CommandLineKey := ParamStr(i);
      end
      else if (Param = '/lines') or (Param = '-lines') then
      begin
        Inc(i);
        if i <= ParamCount then MaxHistoryLines := StrToIntDef(ParamStr(i), DEFAULT_DEBUG_LINES);
      end
      else if (Param = '-infile') or (Param = '/infile') then
      begin
        Inc(i);
        if i <= ParamCount then CommandLineInFile := ParamStr(i);
      end
      else if (Param = '-outfile') or (Param = '/outfile') then
      begin
        Inc(i);
        if i <= ParamCount then CommandLineOutFile := ParamStr(i);
      end
      else if (Param = '/tokens') or (Param = '-tokens') then
      begin
        Inc(i);
        if i <= ParamCount then CommandLineMaxTokens :=
            StrToIntDef(ParamStr(i), DEFAULT_MAX_TOKENS);
      end
      else if (Param = '/debug') or (Param = '-debug') or (Param = '-d') then
      begin
        DebugMode := True;
      end
      else if (Param = '/quiet') or (Param = '-q') then
      begin
        QuietMode := True;
      end
      else if (Param = '/help') or (Param = '-h') or (Param = '--help') then
      begin
        ShowHelp;
      end;
      Inc(i);
    end;
    GlobalConfig.LoadFromINI(ConfigPath);
    if CommandLinePrompt <> '' then GlobalConfig.DefaultPrompt := CommandLinePrompt;
    if CommandLineKey <> '' then GlobalConfig.ApiKey := CommandLineKey;
    if CommandLineModel <> '' then GlobalConfig.ModelName := CommandLineModel;
    if CommandLineOutFile <> '' then GlobalConfig.OutputFile := CommandLineOutFile;
    if CommandLineMaxTokens <> DEFAULT_MAX_TOKENS then
      GlobalConfig.MaxTokens := CommandLineMaxTokens;
    if CommandLineAuthToggle <> '' then
      GlobalConfig.AuthEnabled := (CommandLineAuthToggle = 'true') or
        (CommandLineAuthToggle = '1');
  end;

  procedure ProcessPayload(const InputData: string);
  var
    HttpClient: TFPHTTPClient;
    RequestBody: TStringStream;
    FStream: TFileStream;
    TargetURL: string;
    CustomStream: TAIStreamingStream;
    FinalPrompt: string;
    RootObj: TJSONObject;
    MsgArray: TJSONArray;
    UserMsgObj: TJSONObject;
  begin
    if Trim(GlobalConfig.DefaultPrompt) <> '' then
      FinalPrompt := GlobalConfig.DefaultPrompt + #10#10 + InputData
    else
      FinalPrompt := InputData;
    RootObj := TJSONObject.Create;
    MsgArray := TJSONArray.Create;
    UserMsgObj := TJSONObject.Create;
    try
      UserMsgObj.Add('role', 'user');
      UserMsgObj.Add('content', FinalPrompt);
      MsgArray.Add(UserMsgObj);
      RootObj.Add('model', GlobalConfig.ModelName);
      RootObj.Add('messages', MsgArray);
      RootObj.Add('temperature', 0.2);
      RootObj.Add('stream', True);
      if GlobalConfig.MaxTokens <> -1 then
        RootObj.Add('max_tokens', GlobalConfig.MaxTokens);
      RequestBody := TStringStream.Create(RootObj.AsJSON, TEncoding.UTF8);
    except
      RootObj.Free;
      Exit;
    end;
    TotalBytesOut := RequestBody.Size;
    HttpClient := TFPHTTPClient.Create(nil);
    CustomStream := TAIStreamingStream.Create;
    FullAssembledOutput := '';
    StreamBufferRemainder := '';
    try
      HttpClient.RequestBody := RequestBody;
      HttpClient.AddHeader('Content-Type', 'application/json');
      if GlobalConfig.AuthEnabled and (Trim(GlobalConfig.ApiKey) <> '') then
        HttpClient.AddHeader('Authorization', 'Bearer ' + GlobalConfig.ApiKey);
      TargetURL := Trim(GlobalConfig.LM_URL);
      if (Pos('/v1/chat/completions', TargetURL) = 0) then
      begin
        if (Pos('/v1', TargetURL) > 0) then
          TargetURL := ExcludeTrailingPathDelimiter(TargetURL) + '/chat/completions'
        else
          TargetURL := ExcludeTrailingPathDelimiter(TargetURL) + '/v1/chat/completions';
      end;
      try
        HttpClient.Post(TargetURL, CustomStream);
        if DebugMode then
          RenderDebugConsoleDisplay(True);
        if GlobalConfig.OutputFile <> '' then
        begin
          FStream := TFileStream.Create(GlobalConfig.OutputFile, fmCreate);
          try
            if Length(FullAssembledOutput) > 0 then
              FStream.WriteBuffer(FullAssembledOutput, Length(FullAssembledOutput));
          finally
            FStream.Free;
          end;
        end;
      except
        on E: Exception do
          WriteLn(StdErr, 'Network Error: ', E.Message);
      end;
    finally
      HttpClient.Free;
      RequestBody.Free;
      CustomStream.Free;
      RootObj.Free;
    end;
  end;

  procedure ReadPipelineStream;
  var
    Buffer: string;
    Chunk: string;
    FileStream: TStringList;
    StdinHandle: THandle;
    AvailableBytes, BytesRead: DWORD;
    ReadBuffer: array[0..1023] of char;
    LineAccumulator: string;
    i: integer;
  begin
    Buffer := '';
    if (CommandLineInFile <> '') and FileExists(CommandLineInFile) then
    begin
      FileStream := TStringList.Create;
      try
        FileStream.LoadFromFile(CommandLineInFile);
        Buffer := FileStream.Text;
        if DebugMode then
          InputHistoryList.Text := Buffer;
      finally
        FileStream.Free;
      end;
    end;
    StdinHandle := GetStdHandle(STD_INPUT_HANDLE);
    LineAccumulator := '';
    if DebugMode then SafeClearScreen;
    while True do
    begin
      AvailableBytes := 0;
      if PeekNamedPipe(StdinHandle, nil, 0, nil, @AvailableBytes, nil) and
        (AvailableBytes > 0) then
      begin
        if AvailableBytes > SizeOf(ReadBuffer) then AvailableBytes := SizeOf(ReadBuffer);
        if ReadFile(StdinHandle, ReadBuffer, AvailableBytes, BytesRead, nil) and
          (BytesRead > 0) then
        begin
          TotalBytesOut := TotalBytesOut + BytesRead;
          for i := 0 to BytesRead - 1 do
          begin
            Buffer := Buffer + ReadBuffer[i];
            if DebugMode then
            begin
              if ReadBuffer[i] = #10 then
              begin
                InputHistoryList.Add(LineAccumulator);
                LineAccumulator := '';
              end
              else if ReadBuffer[i] <> #13 then
                LineAccumulator := LineAccumulator + ReadBuffer[i];
            end;
          end;
        end;
      end
      else
      begin
        if (AvailableBytes = 0) and (Buffer <> '') and
          (GetFileType(StdinHandle) = FILE_TYPE_PIPE) then
        begin
          Sleep(20);
          PeekNamedPipe(StdinHandle, nil, 0, nil, @AvailableBytes, nil);
          if AvailableBytes = 0 then Break;
        end;
        if GetFileType(StdinHandle) = FILE_TYPE_CHAR then Sleep(15);
      end;
      if DebugMode then
        RenderDebugConsoleDisplay(False);
    end;
    if (LineAccumulator <> '') and DebugMode then InputHistoryList.Add(LineAccumulator);
    if Trim(Buffer) = '' then Exit;
    ProcessPayload(Buffer);
  end;

begin
  EnableWindowsAnsiFormatting;
  StartTimeTick := GetTickCount;
  InputHistoryList := TStringList.Create;
  OutputHistoryList := TStringList.Create;
  GlobalConfig := TAIProxyConfig.Create;
  try
    ParseCommandLine;
    ReadPipelineStream;
  finally
    GlobalConfig.Free;
    InputHistoryList.Free;
    OutputHistoryList.Free;
  end;
end.
{
[LMStudio]
URL=http://localhost:1234/v1/chat/completions
Port=1234
Model=meta-llama-3-8b-instruct
AuthEnabled=False
ApiKey=
; Explicit max token target window (-1 specifies server model default config window)
MaxTokens=-1

[Output]
FileName=

[Colors]
HeaderColor=\e[1;36m
InputMarkerColor=\e[0;32m
OutputMarkerColor=\e[0;33m
StatsColor=\e[1;35m
}
