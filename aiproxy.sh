#!/bin/bash
# AI generated version of AIProxy from a freepascal (windows) source. AI Proxy is a simple tool that allows the user to pipe content to an AI LLM running on LM Studio.
# For example: ls -lar | aiproxy /prompt "Transform this list of files into a useful report. Include an audit of security permissions. Look for directories and files that are writable by anyone. Tell me how much disk space is being used by different file types. Format the output in a nice table and add color." 
# That command should send it to LM Studio and then output the result through stdout. Use the infile and outfile to save to a file instead.
# AIProxy - CLI Standard I/O Wrapper for Local Inference (Bash version)
# v1.8

# Configuration
PROGRAM_VERSION="v1.8"
DEFAULT_CONFIG="$HOME/.aiproxy.ini"
DEFAULT_LM_URL="http://localhost:1234/v1/chat/completions"
DEFAULT_MODEL="meta-llama-3-8b-instruct"
DEFAULT_PORT=8080
DEFAULT_DEBUG_LINES=10
DEFAULT_MAX_TOKENS=-1
RENDER_THROTTLE_MS=100  # 10Hz smooth dashboard ticker tracking updates

# ANSI colors
DEFAULT_ANSI_HEADER="\e[1;36m"
DEFAULT_ANSI_STATS="\e[1;35m"
DEFAULT_ANSI_INPUT="\e[0;32m"
DEFAULT_ANSI_OUTPUT="\e[0;33m"
DEFAULT_ANSI_RESET="\e[0m"
DEFAULT_ANSI_CLRLN="\e[K"  # Clear to end of line

# Global variables
DebugMode=false
QuietMode=false
CommandLinePrompt=""
CommandLineKey=""
CommandLineModel=""
CommandLineAuthToggle=""
CommandLineInFile=""
CommandLineOutFile=""
CommandLineMaxTokens=$DEFAULT_MAX_TOKENS
MaxHistoryLines=$DEFAULT_DEBUG_LINES

InputHistoryList=()
OutputHistoryList=()
StartTimeTick=$(date +%s%N)
TotalBytesIn=0
TotalBytesOut=0
LastRenderTick=0
StreamBufferRemainder=""
FullAssembledOutput=""

# Function to resolve ANSI escape codes
resolve_ansi_escape_codes() {
    local config_str="$1"
    local fallback="$2"

    if [[ -z "$config_str" ]]; then
        echo "$fallback"
        return
    fi

    echo "${config_str//\\e/\$'\e'}" | sed 's/\033/\$'\''\e'''/g'
}

# Load configuration from INI file
load_config() {
    local file_name="$1"

    if [[ ! -f "$file_name" ]]; then
        return
    fi

    # Read LMStudio section
    while IFS= read -r line; do
        if [[ "$line" == "[LMStudio]" ]]; then
            # Read URL
            while IFS= read -r line; do
                if [[ "$line" == "[Output]" ]]; then
                    break
                fi
                if [[ "$line" == "URL="* ]]; then
                    LM_URL="${line#URL=}"
                    break
                fi
            done

            # Read Port
            while IFS= read -r line; do
                if [[ "$line" == "[Output]" ]]; then
                    break
                fi
                if [[ "$line" == "Port="* ]]; then
                    Port="${line#Port=}"
                    break
                fi
            done

            # Read Model
            while IFS= read -r line; do
                if [[ "$line" == "[Output]" ]]; then
                    break
                fi
                if [[ "$line" == "Model="* ]]; then
                    Model="${line#Model=}"
                    break
                fi
            done

            # Read AuthEnabled
            while IFS= read -r line; do
                if [[ "$line" == "[Output]" ]]; then
                    break
                fi
                if [[ "$line" == "AuthEnabled="* ]]; then
                    AuthEnabled="${line#AuthEnabled=}"
                    break
                fi
            done

            # Read ApiKey
            while IFS= read -r line; do
                if [[ "$line" == "[Output]" ]]; then
                    break
                fi
                if [[ "$line" == "ApiKey="* ]]; then
                    ApiKey="${line#ApiKey=}"
                    break
                fi
            done

            # Read MaxTokens
            while IFS= read -r line; do
                if [[ "$line" == "[Output]" ]]; then
                    break
                fi
                if [[ "$line" == "MaxTokens="* ]]; then
                    MaxTokens="${line#MaxTokens=}"
                    break
                fi
            done

            break
        fi
    done < "$file_name"

    # Read Output section
    while IFS= read -r line; do
        if [[ "$line" == "[Output]" ]]; then
            # Read FileName
            while IFS= read -r line; do
                if [[ "$line" == "[Colors]" ]]; then
                    break
                fi
                if [[ "$line" == "FileName="* ]]; then
                    OutputFile="${line#FileName=}"
                    break
                fi
            done
        fi
    done < "$file_name"

    # Read Colors section
    while IFS= read -r line; do
        if [[ "$line" == "[Colors]" ]]; then
            # Read HeaderColor
            while IFS= read -r line; do
                if [[ -z "$line" ]]; then
                    break
                fi
                if [[ "$line" == "HeaderColor="* ]]; then
                    HeaderColor="${line#HeaderColor=}"
                    break
                fi
            done

            # Read InputMarkerColor
            while IFS= read -r line; do
                if [[ -z "$line" ]]; then
                    break
                fi
                if [[ "$line" == "InputMarkerColor="* ]]; then
                    InputMarkerColor="${line#InputMarkerColor=}"
                    break
                fi
            done

            # Read OutputMarkerColor
            while IFS= read -r line; do
                if [[ -z "$line" ]]; then
                    break
                fi
                if [[ "$line" == "OutputMarkerColor="* ]]; then
                    OutputMarkerColor="${line#OutputMarkerColor=}"
                    break
                fi
            done

            # Read StatsColor
            while IFS= read -r line; do
                if [[ -z "$line" ]]; then
                    break
                fi
                if [[ "$line" == "StatsColor="* ]]; then
                    StatsColor="${line#StatsColor=}"
                    break
                fi
            done

            break
        fi
    done < "$file_name"

    # Resolve ANSI colors
    HeaderColor=$(resolve_ansi_escape_codes "$HeaderColor" "$DEFAULT_ANSI_HEADER")
    InputMarkerColor=$(resolve_ansi_escape_codes "$InputMarkerColor" "$DEFAULT_ANSI_INPUT")
    OutputMarkerColor=$(resolve_ansi_escape_codes "$OutputMarkerColor" "$DEFAULT_ANSI_OUTPUT")
    StatsColor=$(resolve_ansi_escape_codes "$StatsColor" "$DEFAULT_ANSI_STATS")

    # Set defaults if not specified
    if [[ -z "$LM_URL" ]]; then
        LM_URL="$DEFAULT_LM_URL"
    fi
    if [[ -z "$Model" ]]; then
        Model="$DEFAULT_MODEL"
    fi
    if [[ -z "$Port" ]]; then
        Port="$DEFAULT_PORT"
    fi
    if [[ -z "$AuthEnabled" ]]; then
        AuthEnabled=false
    fi
    if [[ -z "$MaxTokens" ]]; then
        MaxTokens="$DEFAULT_MAX_TOKENS"
    fi
}

# Extract streaming delta content from SSE line
extract_streaming_delta_content() {
    local sse_line="$1"
    local token='"delta":{"content":'
    local start_pos
    local end_pos
    local delta_text

    if [[ "$sse_line" != *"$token"* ]]; then
        echo ""
        return
    fi

    start_pos=$(echo "$sse_line" | grep -o -b "$token" | cut -d: -f1)
    ((start_pos += ${#token}))

    # Skip whitespace and quotes
    while [[ "$start_pos" -le ${#sse_line} ]] && { [[ "${sse_line:$start_pos:1}" == " " ]] || [[ "${sse_line:$start_pos:1}" == $'\t' ]] || [[ "${sse_line:$start_pos:1}" == '"' ]]; }; do
        ((start_pos++))
    done

    end_pos=$start_pos
    while [[ "$end_pos" -le ${#sse_line} ]]; do
        if [[ "${sse_line:$end_pos:1}" == '"' ]] && [[ "${sse_line:$end_pos-1:1}" != "\\" ]]; then
            break
        fi
        ((end_pos++))
    done

    delta_text="${sse_line:$start_pos:$((end_pos - start_pos))}"

    # Process escape sequences
    local result=""
    local i=0
    while [[ $i -lt ${#delta_text} ]]; do
        if [[ "${delta_text:$i:1}" == "\\" ]] && [[ $i -lt $(( ${#delta_text} - 1 )) ]]; then
            case "${delta_text:$i+1:1}" in
                "n") result="${result}$(echo -e '\n')";;
                "r") result="$result";;
                "t") result="${result}$(echo -e '\t')";;
                "\"") result="${result}\"";;
                "\\") result="${result}\\";;
                *) result="${result}${delta_text:$i:2}";;
            esac
            ((i += 2))
        else
            result="${result}${delta_text:$i:1}"
            ((i++))
        fi
    done

    echo "$result"
}

# Append token to output history
append_token_to_output_history() {
    local token_text="$1"

    if [[ ${#OutputHistoryList[@]} -eq 0 ]]; then
        OutputHistoryList+=("")
    fi

    for ((i=0; i<${#token_text}; i++)); do
        if [[ "${token_text:$i:1}" == $'\n' ]]; then
            OutputHistoryList+=("")
        elif [[ "${token_text:$i:1}" != $'\r' ]]; then
            OutputHistoryList[${#OutputHistoryList[@]}-1]="${OutputHistoryList[${#OutputHistoryList[@]}-1]}${token_text:$i:1}"
        fi
    done
}

# Clear screen
clear_screen() {
    clear
}

# Reset console cursor
reset_console_cursor() {
    # Not directly supported in bash, but we can use tput for cursor movement
    tput cup 0 0
}

# Render debug console display
render_debug_console_display() {
    local force_render="$1"
    local current_tick=$(date +%s%N)
    local elapsed_ns=$(( (current_tick - StartTimeTick) / 1000000 ))
    local elapsed_secs=$(echo "scale=2; $elapsed_ns / 1000" | bc)

    if [[ "$force_render" == "false" ]]; then
        if [[ $(( (current_tick - LastRenderTick) )) -lt $RENDER_THROTTLE_MS ]]; then
            return
        fi
    fi

    LastRenderTick=$current_tick

    reset_console_cursor

    local hdr_clr="$HeaderColor"
    local in_clr="$InputMarkerColor"
    local out_clr="$OutputMarkerColor"
    local stat_clr="$StatsColor"
    local reset_clr="$DEFAULT_ANSI_RESET"
    local clr_ln="$DEFAULT_ANSI_CLRLN"

    # Condensed Top Summary Dashboard
    echo -e "${stat_clr}[ AIProxy $PROGRAM_VERSION Dashboard ]${reset_clr}${clr_ln}"
    echo -e "${stat_clr}Time: ${reset_clr}${elapsed_secs}s | ${stat_clr}In: ${reset_clr}${TotalBytesIn}B | ${stat_clr}Out: ${reset_clr}${TotalBytesOut}B${clr_ln}"
    echo -e "${stat_clr}Model: ${reset_clr}${Model} | ${stat_clr}Auth: ${reset_clr}${AuthEnabled} | ${stat_clr}MaxTokens: ${reset_clr}${MaxTokens}${clr_ln}"
    echo -e "${clr_ln}"

    # Tail -f Input Panel
    echo -e "${hdr_clr}=========================== INPUT HISTORY (TAIL -F) ===========================${reset_clr}${clr_ln}"

    local start_idx=$(( ${#InputHistoryList[@]} - MaxHistoryLines < 0 ? 0 : ${#InputHistoryList[@]} - MaxHistoryLines ))
    for ((i=start_idx; i<${#InputHistoryList[@]}; i++)); do
        echo -e "${in_clr}>>> ${reset_clr}${InputHistoryList[i]}${clr_ln}"
    done

    # Clear obsolete trailing lines
    if [[ ${#InputHistoryList[@]} -lt $(( start_idx + MaxHistoryLines )) ]]; then
        for ((i=0; i<$(( MaxHistoryLines - (${#InputHistoryList[@]} - start_idx) )); i++)); do
            echo -e "${clr_ln}"
        done
    fi

    echo -e "${clr_ln}"

    # Tail -f Output Panel
    echo -e "${hdr_clr}=========================== OUTPUT HISTORY (TAIL -F) ==========================${reset_clr}${clr_ln}"

    start_idx=$(( ${#OutputHistoryList[@]} - MaxHistoryLines < 0 ? 0 : ${#OutputHistoryList[@]} - MaxHistoryLines ))
    for ((i=start_idx; i<${#OutputHistoryList[@]}; i++)); do
        echo -e "${out_clr}<<< ${reset_clr}${OutputHistoryList[i]}${clr_ln}"
    done

    # Clear obsolete trailing lines
    if [[ ${#OutputHistoryList[@]} -lt $(( start_idx + MaxHistoryLines )) ]]; then
        for ((i=0; i<$(( MaxHistoryLines - (${#OutputHistoryList[@]} - start_idx) )); i++)); do
            echo -e "${clr_ln}"
        done
    fi

    echo -e "${hdr_clr}================================================================================${reset_clr}${clr_ln}"
}

# Enable ANSI formatting (usually enabled by default in most terminals)
enable_ansi_formatting() {
    # This is typically enabled by default in modern terminals
    # No specific action needed in bash
    return
}

# Get config path
get_config_path() {
    echo "$(dirname "$(readlink -f "$0")")/$DEFAULT_CONFIG"
}

# Show help
show_help() {
    echo "AIProxy - CLI Standard I/O Wrapper for Local Inference"
    echo
    echo "Usage: | AIProxy [options] [-outfile output.txt]"
    exit 0
}

# Parse command line
parse_command_line() {
    local i=1
    local config_path=$(get_config_path)

    while [[ $i -le $# ]]; do
        local param=$(echo "$1" | tr '[:upper:]' '[:lower:]')

        if [[ "$param" == "/config" || "$param" == "-config" ]]; then
            i=$((i+1))
            if [[ $i -le $# ]]; then
                config_path="$2"
            fi
        elif [[ "$param" == "/prompt" || "$param" == "-prompt" ]]; then
            i=$((i+1))
            if [[ $i -le $# ]]; then
                CommandLinePrompt="$2"
            fi
        elif [[ "$param" == "/url" || "$param" == "-url" ]]; then
            i=$((i+1))
            if [[ $i -le $# ]]; then
                LM_URL="$2"
            fi
        elif [[ "$param" == "/model" || "$param" == "-model" ]]; then
            i=$((i+1))
            if [[ $i -le $# ]]; then
                CommandLineModel="$2"
            fi
        elif [[ "$param" == "/auth" || "$param" == "-auth" ]]; then
            i=$((i+1))
            if [[ $i -le $# ]]; then
                CommandLineAuthToggle=$(echo "$2" | tr '[:upper:]' '[:lower:]')
            fi
        elif [[ "$param" == "/key" || "$param" == "-key" ]]; then
            i=$((i+1))
            if [[ $i -le $# ]]; then
                CommandLineKey="$2"
            fi
        elif [[ "$param" == "/lines" || "$param" == "-lines" ]]; then
            i=$((i+1))
            if [[ $i -le $# ]]; then
                MaxHistoryLines="$2"
            fi
        elif [[ "$param" == "-infile" || "$param" == "/infile" ]]; then
            i=$((i+1))
            if [[ $i -le $# ]]; then
                CommandLineInFile="$2"
            fi
        elif [[ "$param" == "-outfile" || "$param" == "/outfile" ]]; then
            i=$((i+1))
            if [[ $i -le $# ]]; then
                CommandLineOutFile="$2"
            fi
        elif [[ "$param" == "/tokens" || "$param" == "-tokens" ]]; then
            i=$((i+1))
            if [[ $i -le $# ]]; then
                CommandLineMaxTokens="$2"
            fi
        elif [[ "$param" == "/debug" || "$param" == "-debug" || "$param" == "-d" ]]; then
            DebugMode=true
        elif [[ "$param" == "/quiet" || "$param" == "-q" ]]; then
            QuietMode=true
        elif [[ "$param" == "/help" || "$param" == "-h" || "$param" == "--help" ]]; then
            show_help
        fi

        i=$((i+1))
    done

    load_config "$config_path"

    if [[ -n "$CommandLinePrompt" ]]; then
        DefaultPrompt="$CommandLinePrompt"
    fi
    if [[ -n "$CommandLineKey" ]]; then
        ApiKey="$CommandLineKey"
    fi
    if [[ -n "$CommandLineModel" ]]; then
        Model="$CommandLineModel"
    fi
    if [[ -n "$CommandLineOutFile" ]]; then
        OutputFile="$CommandLineOutFile"
    fi
    if [[ "$CommandLineMaxTokens" != "$DEFAULT_MAX_TOKENS" ]]; then
        MaxTokens="$CommandLineMaxTokens"
    fi
    if [[ -n "$CommandLineAuthToggle" ]]; then
        AuthEnabled=$(echo "$CommandLineAuthToggle" | grep -E '^(true|1)$')
    fi
}

# Process payload
process_payload() {
    local input_data="$1"
    local final_prompt

    if [[ -n "$DefaultPrompt" ]]; then
        final_prompt="$DefaultPrompt

$input_data"
    else
        final_prompt="$input_data"
    fi

    # Create JSON payload
    local json_payload=$(jq -n \
        --arg model "$Model" \
        --arg messages "$(jq -n --arg content "$final_prompt" '{"role": "user", "content": $content}')" \
        --arg temperature "0.2" \
        --arg stream "true" \
        --arg max_tokens "$MaxTokens" \
        '{model: $model, messages: [$messages], temperature: $temperature, stream: $stream, max_tokens: $max_tokens}')

    TotalBytesOut=$(( ${#json_payload} ))

    # Make HTTP request
    local response
    local delta_text

    if [[ "$AuthEnabled" == "true" && -n "$ApiKey" ]]; then
        response=$(curl -s -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer $ApiKey" \
            -d "$json_payload" \
            "$LM_URL")
    else
        response=$(curl -s -X POST \
            -H "Content-Type: application/json" \
            -d "$json_payload" \
            "$LM_URL")
    fi

    # Process streaming response
    while IFS= read -r line; do
        if [[ "$line" == "data: [DONE]" ]]; then
            continue
        fi

        delta_text=$(extract_streaming_delta_content "$line")
        if [[ -n "$delta_text" ]]; then
            FullAssembledOutput="${FullAssembledOutput}${delta_text}"

            if [[ "$DebugMode" == "true" ]]; then
                append_token_to_output_history "$delta_text"
                render_debug_console_display "false"
            else
                echo -n "$delta_text"
                echo -ne "\033[K"  # Clear line (similar to System.Flush(Output))
            fi
        fi
    done <<< "$response"

    # Write to output file if specified
    if [[ -n "$OutputFile" ]]; then
        echo -n "$FullAssembledOutput" > "$OutputFile"
    fi
}

# Read pipeline stream
read_pipeline_stream() {
    local buffer=""
    local line_accumulator=""

    if [[ -n "$CommandLineInFile" && -f "$CommandLineInFile" ]]; then
        buffer=$(cat "$CommandLineInFile")
        if [[ "$DebugMode" == "true" ]]; then
            while IFS= read -r line; do
                InputHistoryList+=("$line")
            done < <(echo "$buffer")
        fi
    fi

    if [[ "$DebugMode" == "true" ]]; then
        clear_screen
    fi

    while true; do
        if [[ -n "$buffer" ]]; then
            # Process the buffer
            if [[ "$DebugMode" == "true" ]]; then
                render_debug_console_display "false"
            fi

            if [[ -n "$line_accumulator" && "$DebugMode" == "true" ]]; then
                InputHistoryList+=("$line_accumulator")
            fi

            if [[ -n "$(echo "$buffer" | xargs)" ]]; then
                process_payload "$buffer"
            fi
        fi

        # Read from stdin
        if read -r -n 1 -t 0.1 char; then
            buffer="${buffer}${char}"
            if [[ "$DebugMode" == "true" ]]; then
                if [[ "$char" == $'\n' ]]; then
                    line_accumulator=""
                elif [[ "$char" != $'\r' ]]; then
                    line_accumulator="${line_accumulator}${char}"
                fi
            fi
        else
            # Timeout, check if we should exit
            if [[ -z "$buffer" ]]; then
                # No data, check if we're reading from a pipe
                if [[ -p /dev/stdin ]]; then
                    sleep 0.2
                else
                    break
                fi
            fi
        fi
    done
}

# Main function
main() {
    enable_ansi_formatting
    StartTimeTick=$(date +%s%N)
    DefaultPrompt=""
    ApiKey=""
    Model="$DEFAULT_MODEL"
    Port="$DEFAULT_PORT"
    AuthEnabled=false
    MaxTokens="$DEFAULT_MAX_TOKENS"
    OutputFile=""
    HeaderColor="$DEFAULT_ANSI_HEADER"
    InputMarkerColor="$DEFAULT_ANSI_INPUT"
    OutputMarkerColor="$DEFAULT_ANSI_OUTPUT"
    StatsColor="$DEFAULT_ANSI_STATS"

    parse_command_line "$@"
    read_pipeline_stream
}

# Check if jq is installed
if ! command -v jq &> /dev/null; then
    echo "Error: jq is required but not installed. Please install it."
    exit 1
fi

# Run the main function with all command line arguments
main "$@"
