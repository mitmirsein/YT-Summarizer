# app.py (v.2.0)

import sys
import os
import re
import json
import yt_dlp
import requests
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv, find_dotenv, set_key

# --- 앱 버전 정의 ---
APP_VERSION = "v.2.0"

# --- 초기 설정 및 환경 변수 로드 ---
dotenv_path = find_dotenv()
if dotenv_path:
    load_dotenv(dotenv_path)

app = Flask(__name__)

# 프로그램 기본 설정
app_settings = {
    "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", ""),
    "GEMINI_TEMPERATURE": float(os.getenv("GEMINI_TEMPERATURE", 0.5)),
    "AVAILABLE_MODELS": ["gemini-2.5-pro", "gemini-2.5-flash"],
    "DEFAULT_MODEL": "gemini-2.5-pro",
    "DEFAULT_PROMPT": "다음 대본을 400자에서 500자 사이의 한글로, 한 단락으로 요약해 주세요.\n\n---\n{transcript}\n---"
}


# --- 핵심 로직 함수 ---
def get_video_info(video_url):
    """yt-dlp를 사용해 비디오 제목과 한글 자막을 추출합니다."""
    temp_file_base = "temp_subtitle_data"
    ydl_opts = {
        'writeautomaticsub': True, 'subtitleslangs': ['ko'], 'skip_download': True,
        'convertsubtitles': 'vtt', 'outtmpl': f"{temp_file_base}.%(ext)s",
        'quiet': True, 'no_warnings': True, 'encoding': 'utf-8'
    }
    video_title, transcript_text, temp_vtt_file = "알 수 없는 제목", "", f"{temp_file_base}.ko.vtt"
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
            info_dict = ydl.extract_info(video_url, download=False)
            video_title = info_dict.get('title', '알 수 없는 제목')
        with yt_dlp.YoutubeDL(ydl_opts) as ydl: ydl.download([video_url])
        if not os.path.exists(temp_vtt_file): raise FileNotFoundError("한글 자막 파일을 찾을 수 없습니다.")
        previous_line, transcript_content = "", []
        with open(temp_vtt_file, "r", encoding="utf-8") as f_in:
            for line in f_in:
                line = line.strip()
                if not line or "-->" in line or line.startswith("WEBVTT"): continue
                cleaned_line = re.sub(r'<[^>]+>', '', line)
                if cleaned_line and cleaned_line != previous_line:
                    transcript_content.append(cleaned_line)
                    previous_line = cleaned_line
        transcript_text = " ".join(transcript_content)
    finally:
        if os.path.exists(temp_vtt_file): os.remove(temp_vtt_file)
    return video_title, transcript_text

def summarize_with_gemini(transcript_text, api_key, model, prompt_template, temperature):
    """Gemini API를 호출하여 텍스트를 요약합니다."""
    if not api_key: raise ValueError("Gemini API 키가 제공되지 않았습니다.")
    prompt = prompt_template.format(transcript=transcript_text)
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature
        }
    }
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.post(api_url, headers=headers, data=json.dumps(payload), timeout=90)
        response.raise_for_status()
        result = response.json()
        if 'candidates' in result and result.get('candidates'):
            return result['candidates'][0]['content']['parts'][0]['text']
        return "API에서 유효한 요약을 생성하지 못했습니다. (내용 필터링 가능성)"
    except requests.exceptions.RequestException as e: raise ConnectionError(f"Gemini API 통신 오류: {e}")
    except (KeyError, IndexError) as e: raise ValueError(f"Gemini API 응답 파싱 오류: {e}")


# --- Flask 라우트 정의 ---
@app.route('/')
def index():
    return render_template('index.html', app_version=APP_VERSION)

@app.route('/api/settings', methods=['GET', 'POST'])
def manage_settings():
    """설정을 가져오고, 영구적으로 업데이트합니다."""
    if request.method == 'GET':
        setup_needed = not bool(app_settings['GEMINI_API_KEY'])
        return jsonify({
            'apiKey': app_settings['GEMINI_API_KEY'],
            'temperature': app_settings['GEMINI_TEMPERATURE'],
            'availableModels': app_settings['AVAILABLE_MODELS'],
            'defaultModel': app_settings['DEFAULT_MODEL'],
            'prompt': app_settings['DEFAULT_PROMPT'],
            'setup_needed': setup_needed 
        })
    elif request.method == 'POST':
        data = request.get_json()
        new_api_key = data.get('apiKey')
        new_temperature = float(data.get('temperature', 0.5))

        # 1. 현재 세션 메모리 업데이트
        app_settings['GEMINI_API_KEY'] = new_api_key
        app_settings['GEMINI_TEMPERATURE'] = new_temperature
        app_settings['DEFAULT_MODEL'] = data.get('model', app_settings['DEFAULT_MODEL'])
        app_settings['DEFAULT_PROMPT'] = data.get('prompt', app_settings['DEFAULT_PROMPT'])

        # 2. .env 파일 영구 저장
        try:
            dotenv_path_to_set = find_dotenv()
            if not dotenv_path_to_set: dotenv_path_to_set = os.path.join(os.getcwd(), '.env')
            set_key(dotenv_path_to_set, "GEMINI_API_KEY", new_api_key)
            set_key(dotenv_path_to_set, "GEMINI_TEMPERATURE", str(new_temperature))
            return jsonify({'message': '설정이 성공적으로 저장되었고, 기본값으로 업데이트되었습니다.'})
        except Exception as e:
            app.logger.error(f".env 파일 저장 실패: {e}")
            return jsonify({'message': '설정이 임시 저장되었습니다 (.env 파일 업데이트 실패).'})

@app.route('/api/setup', methods=['POST'])
def initial_setup():
    """최초 API 키를 설정하고 .env 파일을 생성합니다."""
    data = request.get_json()
    api_key = data.get('apiKey')
    if not api_key: return jsonify({'error': 'API 키가 제공되지 않았습니다.'}), 400
    try:
        dotenv_path_to_set = find_dotenv()
        if not dotenv_path_to_set:
            dotenv_path_to_set = os.path.join(os.getcwd(), '.env')
        set_key(dotenv_path_to_set, "GEMINI_API_KEY", api_key)
        app_settings['GEMINI_API_KEY'] = api_key
        return jsonify({'message': 'API 키가 성공적으로 저장되었습니다. 이제 앱을 사용할 수 있습니다.'})
    except Exception as e:
        return jsonify({'error': f'파일 저장 중 오류 발생: {e}'}), 500

@app.route('/summarize', methods=['POST'])
def summarize():
    """요약 요청을 처리합니다."""
    data = request.get_json()
    video_url = data.get('url')
    model_to_use = data.get('model', app_settings['DEFAULT_MODEL'])
    temperature_to_use = float(data.get('temperature', app_settings['GEMINI_TEMPERATURE']))

    if not video_url: return jsonify({'error': 'URL이 제공되지 않았습니다.'}), 400
    try:
        video_title, transcript = get_video_info(video_url)
        if not transcript.strip(): return jsonify({'error': '영상에서 텍스트를 추출할 수 없었습니다. (자막 없음)'}), 400
        summary_text = summarize_with_gemini(
            transcript,
            api_key=app_settings['GEMINI_API_KEY'],
            model=model_to_use,
            prompt_template=app_settings['DEFAULT_PROMPT'],
            temperature=temperature_to_use
        )
        return jsonify({'title': video_title, 'summary': summary_text, 'original_url': video_url})
    except Exception as e:
        app.logger.error(f"오류: {e}", exc_info=True)
        return jsonify({'error': f'서버 오류: {e}'}), 500

# --- 서버 실행 ---
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')