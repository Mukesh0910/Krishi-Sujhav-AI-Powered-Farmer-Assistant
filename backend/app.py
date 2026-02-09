from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory, send_file, Response
from flask_cors import CORS
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename
import os
import json
from datetime import datetime
from dotenv import load_dotenv
import re
import random
from typing import Dict, List, Generator
import requests
from pathlib import Path
import uuid
import threading
import time
import base64
from io import BytesIO
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import secrets

try:
    import google.generativeai as genai
except Exception as e:
    genai = None
    print(f" Optional package google.generativeai not available or failed to import: {e}")

# Import document extractor (using fast, lightweight version)
try:
    from simple_document_extractor import FastDocumentExtractor
    document_extractor = FastDocumentExtractor()
    print("  Fast document extractor initialized")
except Exception as e:
    document_extractor = None
    print(f"  Document extractor not available: {e}")

# Import farmer services (Mandi, Schemes, Calendar, Soil, Economics, Alerts)
try:
    from farmer_services import (
        mandi_service, scheme_service, crop_calendar_service,
        soil_service, economics_service, alert_service
    )
    print("  Farmer services module loaded successfully")
except Exception as e:
    mandi_service = scheme_service = crop_calendar_service = None
    soil_service = economics_service = alert_service = None
    print(f"  Farmer services not available: {e}")

# Load environment variables
load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'frontend', 'templates'),
    static_folder=os.path.join(BASE_DIR, 'frontend', 'static')
)

app.config["TEMPLATES_AUTO_RELOAD"] = True
# ========== SMTP Configuration ==========
SMTP_EMAIL = os.getenv('SMTP_EMAIL', '')       # Your Gmail address
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '') # Gmail App Password (NOT your regular password)
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 587

# OTP storage: { email: { 'otp': '123456', 'expiry': datetime, 'verified': False, 'attempts': 0, 'token': 'uuid' } }
otp_store = {}
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24))

# Enable CORS for frontend-backend communication
CORS(app)

bcrypt = Bcrypt(app)

# ----------------------
# PostgreSQL Setup (Render.com / Production)
# ----------------------
import psycopg2
import psycopg2.extras
from urllib.parse import urlparse

def get_connection():
    """Get PostgreSQL connection using DATABASE_URL (Render) or individual env vars"""
    try:
        database_url = os.getenv('DATABASE_URL', '')
        
        if database_url:
            # Render provides DATABASE_URL (Internal URL for same-region services)
            # Fix: Render gives postgres:// but psycopg2 needs postgresql://
            if database_url.startswith('postgres://'):
                database_url = database_url.replace('postgres://', 'postgresql://', 1)
            conn = psycopg2.connect(database_url)
        else:
            # Fallback to individual env vars (local dev)
            conn = psycopg2.connect(
                host=os.getenv("PGHOST"),
                port=os.getenv("PGPORT"),
                database=os.getenv("PGDATABASE"),
                user=os.getenv("PGUSER"),
                password=os.getenv("PGPASSWORD"),
                sslmode="require"
            )
        
        conn.autocommit = False
        return conn
    except Exception as e:
        print(f" Database connection failed: {e}")
        return None

def init_db():
    try:
        conn = get_connection()
        if conn is None:
            raise Exception("No DB connection")

        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("Database initialized successfully")

    except Exception as e:
        print("Database init failed:", e)

init_db()


def execute_query(query, params=None, fetch=False):
    """Execute SQL query with optional parameters (PostgreSQL compatible).
    Converts ? placeholders to %s for psycopg2."""
    conn = get_connection()
    if not conn:
        return None
    try:
        # Convert SQL Server style ? placeholders to PostgreSQL %s
        query = query.replace('?', '%s')
        
        cursor = conn.cursor()
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        if fetch:
            results = cursor.fetchall()
            cursor.close()
            conn.close()
            return results
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        print(f" SQL Query Error: {e}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
            except:
                pass
            conn.close()
        return None

# Initialize database tables (PostgreSQL)
def init_database():
    """Create tables if they don't exist (PostgreSQL)"""
    conn = get_connection()
    if not conn:
        print(" Cannot initialize database — no connection")
        return
    try:
        cursor = conn.cursor()
        
        # Create Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS Users (
                UserID SERIAL PRIMARY KEY,
                FullName VARCHAR(200) NOT NULL,
                Email VARCHAR(200) UNIQUE NOT NULL,
                PasswordHash VARCHAR(500) NOT NULL,
                PreferredLanguage VARCHAR(10) DEFAULT 'en',
                Location VARCHAR(200),
                Role VARCHAR(50) DEFAULT 'Farmer',
                CreatedAt TIMESTAMP DEFAULT NOW(),
                LastLogin TIMESTAMP
            )
        """)
        
        # Create Chats table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS Chats (
                ID SERIAL PRIMARY KEY,
                UserID INTEGER REFERENCES Users(UserID) ON DELETE CASCADE,
                UserMessage TEXT,
                AIResponse TEXT,
                Language VARCHAR(10) DEFAULT 'en',
                HasImages INTEGER DEFAULT 0,
                DetectedDiseases TEXT,
                Timestamp TIMESTAMP DEFAULT NOW(),
                SessionID VARCHAR(100)
            )
        """)
        
        # Create indexes for performance
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chats_userid ON Chats(UserID)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chats_sessionid ON Chats(SessionID)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_users_email ON Users(Email)
        """)
        
        conn.commit()
        cursor.close()
        conn.close()
        print(" Database tables initialized (PostgreSQL)")
    except Exception as e:
        print(f" Database init error: {e}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
            except:
                pass
            conn.close()

# Test database connection on startup
try:
    test_conn = get_connection()
    if test_conn:
        test_conn.close()
        print(" Connected to PostgreSQL successfully!")
        db_url = os.getenv('DATABASE_URL', 'local')
        print(f"   Source: {'Render DATABASE_URL' if db_url != 'local' else 'Local PostgreSQL'}")
        print("   Using psycopg2 driver")
        # Initialize tables
        init_database()
    else:
        print(" Failed to connect to PostgreSQL")
except Exception as e:
    print(f" Database connection test failed: {e}")

# ----------------------
# Session Management Functions
# ----------------------
SESSION_SIZE_LIMIT_MB = 16  # 16MB limit per chat session

def create_new_session():
    """Generate a new unique session ID"""
    return str(uuid.uuid4())

def get_active_session():
    """Get the current active session ID from Flask session"""
    if 'active_chat_session' not in session:
        session['active_chat_session'] = create_new_session()
    return session['active_chat_session']

def set_active_session(session_id):
    """Set the active session ID"""
    session['active_chat_session'] = session_id

def calculate_session_size(session_id):
    """Calculate total size of messages in a session (in MB)"""
    try:
        query = """
            SELECT COALESCE(SUM(LENGTH(UserMessage) + LENGTH(AIResponse)), 0) / 1024.0 / 1024.0 as SizeMB
            FROM Chats 
            WHERE SessionID = ?
        """
        result = execute_query(query, [session_id], fetch=True)
        if result and result[0][0]:
            return float(result[0][0])
        return 0.0
    except Exception as e:
        print(f"Error calculating session size: {e}")
        return 0.0

def validate_session_size(session_id):
    """Check if session is under 16MB limit. Returns (is_valid, current_size_mb)"""
    current_size = calculate_session_size(session_id)
    is_valid = current_size < SESSION_SIZE_LIMIT_MB
    return is_valid, current_size

def get_user_sessions(user_email):
    """Get all chat sessions for a user with metadata"""
    try:
        # Get user ID
        user_query = "SELECT UserID FROM Users WHERE Email = ?"
        user_result = execute_query(user_query, [user_email], fetch=True)
        
        if not user_result:
            return []
        
        user_id = user_result[0][0]
        
        # Get sessions with first message as title and message count
        sessions_query = """
            SELECT DISTINCT 
                SessionID,
                MIN(Timestamp) as FirstMessageTime,
                COUNT(*) as MessageCount,
                (SELECT UserMessage FROM Chats WHERE SessionID = c.SessionID ORDER BY Timestamp ASC LIMIT 1) as FirstMessage
            FROM Chats c
            WHERE UserID = ? AND SessionID IS NOT NULL
            GROUP BY SessionID
            ORDER BY MIN(Timestamp) DESC
        """
        sessions_result = execute_query(sessions_query, [user_id], fetch=True)
        
        sessions = []
        if sessions_result:
            for row in sessions_result:
                session_id = row[0]
                first_time = row[1]
                msg_count = row[2]
                first_msg = row[3] or "New Chat"
                
                # Generate session title (first 40 chars of first message)
                title = first_msg[:40] + "..." if len(first_msg) > 40 else first_msg
                
                # Calculate size
                size_mb = calculate_session_size(session_id)
                
                sessions.append({
                    'session_id': session_id,
                    'title': title,
                    'message_count': msg_count,
                    'first_message_time': first_time.isoformat() if first_time else None,
                    'size_mb': round(size_mb, 2),
                    'is_full': size_mb >= SESSION_SIZE_LIMIT_MB
                })
        
        return sessions
    except Exception as e:
        print(f"Error getting user sessions: {e}")
        import traceback
        traceback.print_exc()
        return []

# ----------------------
# Gemini AI Setup
# ----------------------
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
OPENWEATHER_API_KEY = os.getenv('OPENWEATHER_API_KEY', '')  # Add OpenWeather API key

# Only attempt to configure Gemini if the google.generativeai package was imported
if GEMINI_API_KEY and GEMINI_API_KEY != '' and genai is not None:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-2.0-flash')
        print(" Gemini AI configured successfully with gemini-2.0-flash!")
    except Exception as e:
        print(f" Failed to configure Gemini AI: {e}")
        gemini_model = None
else:
    if GEMINI_API_KEY and GEMINI_API_KEY != '' and genai is None:
        print(" Gemini API key provided but google.generativeai package is unavailable; using fallback responses.")
    else:
        print(" Gemini API key not found or is placeholder. Using fallback responses.")
    gemini_model = None

# ----------------------
# File Upload Configuration
# ----------------------
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'csv', 'xlsx', 'wav', 'mp3', 'ogg', 'pptx'}
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Create uploads directory if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ----------------------
# Gemini REST fallback (uses HTTP REST when the google.generativeai package is unavailable)
# ----------------------
def call_gemini_rest(prompt_text: str, language: str = 'en') -> str:
    """Call Google Generative Language REST endpoint directly using API key from env.
    Returns the generated text or raises an exception on failure.
    """
    if not GEMINI_API_KEY or GEMINI_API_KEY == '':
        raise RuntimeError('No GEMINI_API_KEY configured')

    # Use Gemini 2.0 Flash model and match JS payload/response
    model = 'gemini-2.0-flash'
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt_text
                    }
                ]
            }
        ]
    }

    headers = {"Content-Type": "application/json"}

    try:
        print(f" Calling Gemini REST API (2.0-flash, prompt length={len(prompt_text)} chars)")
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        print(f" Gemini REST response status: {resp.status_code}")

        if resp.status_code != 200:
            print(f" REST API error: {resp.text[:500]}")
            resp.raise_for_status()

        data = resp.json()
        print(f" Response structure: {list(data.keys())}")

        # Match JS parsing logic
        if (
            "candidates" in data and
            isinstance(data["candidates"], list) and
            len(data["candidates"]) > 0 and
            "content" in data["candidates"][0] and
            "parts" in data["candidates"][0]["content"] and
            len(data["candidates"][0]["content"]["parts"]) > 0 and
            "text" in data["candidates"][0]["content"]["parts"][0]
        ):
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            print(f" Successfully extracted response (length={len(text)} chars)")
            return text.strip()

        print(f" Unexpected response format: {str(data)[:300]}")
        raise RuntimeError(f"Could not parse Gemini response: {data}")

    except requests.exceptions.RequestException as e:
        print(f" Network error calling Gemini REST API: {e}")
        raise
    except Exception as e:
        print(f" Error calling Gemini REST API: {e}")
        raise

# ----------------------
# Translation System
# ----------------------
def load_translations():
    translations_path = os.path.join(os.path.dirname(__file__), '../frontend/translations/translations.json')
    try:
        with open(translations_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(" Translations file not found. Using default English.")
        return {"en": {}}

translations = load_translations()

def get_translation(key, lang='en'):
    if lang in translations and key in translations[lang]:
        return translations[lang][key]
    elif key in translations.get('en', {}):
        return translations['en'][key]
    return key

def convert_language_code(language):
    language_mappings = {
        'english': 'en', 'en': 'en',
        'hindi': 'hi', 'hi': 'hi', 
        'marathi': 'mr', 'mr': 'mr',
        'punjabi': 'pa', 'pa': 'pa',
        'malayalam': 'ml', 'ml': 'ml',
        'tamil': 'ta', 'ta': 'ta',
        'telugu': 'te', 'te': 'te',
        'kannada': 'kn', 'kn': 'kn'
    }
    return language_mappings.get(language.lower(), 'en')

def get_user_language():
    if 'user_email' in session:
        try:
            query = "SELECT PreferredLanguage FROM Users WHERE Email = ?"
            result = execute_query(query, [session['user_email']], fetch=True)
            if result and result[0][0]:
                return convert_language_code(result[0][0])
        except:
            pass
    
    return convert_language_code(session.get('language', 'english'))

# ----------------------
# Weather Cache (to avoid rate limits)
# ----------------------
weather_cache = {}
WEATHER_CACHE_DURATION = 300  # 5 minutes in seconds

def get_cached_weather(location):
    """Get cached weather data if available and not expired"""
    if location in weather_cache:
        cached_data, timestamp = weather_cache[location]
        if (datetime.now() - timestamp).total_seconds() < WEATHER_CACHE_DURATION:
            print(f" Using cached weather data for {location}")
            return cached_data
    return None

def cache_weather(location, data):
    """Cache weather data with timestamp"""
    weather_cache[location] = (data, datetime.now())

# ----------------------
# Farming AI Knowledge Base
# ----------------------
class FarmingAI:
    def __init__(self):
        # Farming-related keywords for filtering
        self.farming_keywords = [
            # Crops and plants
            'crop', 'crops', 'plant', 'plants', 'seed', 'seeds', 'farming', 'agriculture', 'agricultural',
            'wheat', 'rice', 'corn', 'maize', 'barley', 'soybean', 'cotton', 'sugarcane', 'potato', 'tomato',
            'vegetable', 'vegetables', 'fruit', 'fruits', 'grain', 'grains', 'cereal', 'pulses', 'legume',
            'onion', 'garlic', 'cabbage', 'cauliflower', 'carrot', 'beans', 'peas', 'chili', 'pepper',
            'banana', 'mango', 'apple', 'orange', 'grape', 'papaya', 'guava', 'pomegranate',
            # Farming activities
            'harvest', 'harvesting', 'planting', 'sowing', 'cultivation', 'growing', 'pruning', 'weeding',
            'transplanting', 'mulching', 'tilling', 'hoeing',
            # Soil and fertilizers
            'soil', 'fertilizer', 'fertilizers', 'manure', 'compost', 'nutrients', 'nitrogen', 'phosphorus', 
            'potassium', 'npk', 'organic', 'vermicompost', 'urea', 'dap', 'mop', 'ssp', 'zinc', 'sulphur',
            'sulfur', 'boron', 'calcium', 'magnesium', 'micronutrient', 'micronutrients', 'fertiliser',
            # Pests and diseases
            'pest', 'pests', 'disease', 'diseases', 'insect', 'insects', 'bug', 'bugs', 'fungus', 'fungi',
            'blight', 'rot', 'wilt', 'mold', 'mould', 'infection', 'pesticide', 'herbicide', 'fungicide',
            'aphid', 'caterpillar', 'worm', 'beetle', 'locust', 'termite', 'nematode',
            # Water and irrigation
            'irrigation', 'water', 'watering', 'drought', 'rain', 'rainfall', 'monsoon', 'drip', 'sprinkler',
            # Weather and climate
            'weather', 'climate', 'season', 'seasonal', 'temperature', 'humidity',
            # Market and economics
            'market', 'price', 'prices', 'selling', 'buying', 'profit', 'cost', 'economics', 'income', 'yield',
            # Livestock and poultry
            'livestock', 'cattle', 'cow', 'cows', 'buffalo', 'goat', 'goats', 'sheep', 'chicken', 'poultry',
            'dairy', 'milk', 'egg', 'eggs', 'meat', 'animal', 'fodder', 'feed',
            # Farm equipment
            'tractor', 'plow', 'plough', 'equipment', 'machinery', 'tools',
            # General farming terms
            'farm', 'farmer', 'field', 'land', 'plot', 'greenhouse', 'nursery', 'garden', 'orchard',
            'रोपण', 'कटाई', 'सिंचाई', 'बीमारी', 'उपचार', 'treatment', 'cure', 'remedy',
            'खेती', 'फसल', 'किसान', 'बीज', 'खाद', 'पानी', 'रोग', 'कीट'  # Hindi keywords
        ]
        
        # Non-farming topics to explicitly reject
        self.non_farming_topics = [
            'movie', 'movies', 'film', 'cinema', 'actor', 'actress', 'song', 'music', 'singer',
            'cricket', 'football', 'sports', 'game', 'match', 'player',
            'politics', 'election', 'government', 'minister', 'president',
            'recipe', 'cooking', 'food preparation', 'restaurant',
            'programming', 'code', 'software', 'computer', 'laptop', 'phone',
            'love', 'romance', 'dating', 'relationship',
            'math', 'physics', 'chemistry', 'history', 'geography',
            'stock market', 'cryptocurrency', 'bitcoin'
        ]
    
    def is_farming_related(self, message: str) -> bool:
        """Check if the message is related to farming/agriculture.
        Lenient approach: allow ambiguous queries through to Gemini, only reject clearly non-farming topics.
        """
        msg = (message or "").lower().strip()
        
        # Empty message check
        if not msg:
            return False
        
        # Check for greetings first (always allow) - use word boundaries to avoid false matches
        import re
        greetings = [r'\bhello\b', r'\bhi\b', r'\bnamaste\b', r'\bhey\b', r'\bhii\b', r'\bhelp\b', r'\bstart\b', 'नमस्ते', 'नमस्कार', 'good morning', 'good afternoon', 'good evening']
        if any(re.search(pattern, msg) if pattern.startswith(r'\b') else pattern in msg for pattern in greetings):
            return True
        
        # Check for farming keywords (explicit acceptance)
        for farming_word in self.farming_keywords:
            if farming_word in msg:
                return True
        
        # Check for non-farming topics (explicit rejection) — only reject if NO farming context at all
        non_farming_score = 0
        for non_farming_word in self.non_farming_topics:
            if non_farming_word in msg:
                non_farming_score += 1
        
        # Only reject if clearly non-farming (multiple non-farming keywords and zero farming keywords)
        if non_farming_score >= 2:
            return False
        
        # For single non-farming keyword, still reject
        if non_farming_score == 1:
            return False
        
        # For ambiguous messages (no clear farming or non-farming keywords),
        # ALLOW them through to Gemini — let the AI decide if it's farming-related
        # This prevents rejecting legitimate farming queries that don't use exact keywords
        # Examples: "what should I do this season?", "how to increase income?", "suggest something"
        print(f"  Ambiguous query — allowing through to Gemini: {msg[:50]}...")
        return True

    def is_update_query(self, message: str) -> bool:
        """Check if user is asking for current updates/news about farming topics"""
        msg = (message or "").lower().strip()
        
        # Keywords indicating update/news queries
        update_keywords = [
            'update', 'updates', 'current', 'latest', 'recent', 'news', 'today', 'now',
            'what\'s new', 'whats new', 'happening', 'going on', 'this week', 'this month',
            'currently', 'present', 'nowadays', 'these days', 'right now',
            'अपडेट', 'समाचार', 'ताज़ा', 'नवीनतम', 'वर्तमान', 'अभी', 'आज',
            'अद्यतन', 'बातमी', 'ताज्या', 'सध्या', 'आत्ता'  # Marathi
        ]
        
        # Check if message contains update keywords
        has_update_keyword = any(keyword in msg for keyword in update_keywords)
        
        # Check if message is farming-related
        has_farming_keyword = any(farming_word in msg for farming_word in self.farming_keywords)
        
        return has_update_keyword and has_farming_keyword

    def is_weather_query(self, message: str) -> bool:
        """Check if user is asking about weather"""
        msg = (message or "").lower().strip()
        
        # Weather-specific keywords
        weather_keywords = [
            'weather', 'rain', 'raining', 'rainfall', 'temperature', 'climate', 'forecast',
            'snow', 'snowing', 'monsoon', 'humidity', 'wind', 'storm', 'cyclone',
            'hot', 'cold', 'sunny', 'cloudy', 'will it rain', 'going to rain',
            'मौसम', 'बारिश', 'वर्षा', 'तापमान', 'जलवायु', 'पूर्वानुमान',
            'हवामान', 'पाऊस', 'तापमान', 'अंदाज',  # Marathi
            'ਮੌਸਮ', 'ਬਾਰਿਸ਼', 'ਤਾਪਮਾਨ',  # Punjabi
        ]
        
        return any(keyword in msg for keyword in weather_keywords)
    
    def is_crop_recommendation_query(self, message: str) -> bool:
        """Check if user is asking for crop recommendations based on weather/location"""
        msg = (message or "").lower().strip()
        
        # Crop recommendation keywords (includes variations)
        crop_keywords = ['crop', 'crops', 'plant', 'grow', 'cultivat', 'suitable', 'best', 'recommend', 'sow', 'seed', 'farming', 'फसल', 'पीक', 'ਫਸਲ', 'बोना', 'उगाना']
        weather_location_keywords = ['weather', 'climate', 'season', 'location', 'area', 'region', 'here', 'my location', 'current', 'मौसम', 'जगह', 'क्षेत्र', 'हवामान', 'ਮੌਸਮ']
        soil_keywords = ['soil', 'land', 'मिट्टी', 'जमीन', 'माती', 'ਮਿੱਟੀ']
        
        has_crop = any(keyword in msg for keyword in crop_keywords)
        has_context = any(keyword in msg for keyword in weather_location_keywords + soil_keywords)
        
        return has_crop and has_context
    
    def is_mandi_price_query(self, message: str) -> bool:
        """Check if user is asking about mandi/market prices or MSP"""
        msg = (message or "").lower().strip()
        price_keywords = [
            'mandi', 'market price', 'rate', 'bhav', 'msp', 'minimum support price',
            'selling price', 'what price', 'commodity price', 'grain price',
            'मंडी', 'भाव', 'दाम', 'बाजार', 'कीमत', 'रेट', 'न्यूनतम समर्थन मूल्य',
            'बाजार भाव', 'बाजारभाव', 'किंमत', 'दर', 'बाजार दर',
            'ਮੰਡੀ', 'ਭਾਅ', 'ਕੀਮਤ', 'ਰੇਟ',
        ]
        return any(keyword in msg for keyword in price_keywords)
    
    def is_scheme_query(self, message: str) -> bool:
        """Check if user is asking about government schemes"""
        msg = (message or "").lower().strip()
        scheme_keywords = [
            'scheme', 'yojana', 'pm-kisan', 'pmkisan', 'pm kisan', 'pmfby',
            'kcc', 'kisan credit', 'subsidy', 'government scheme', 'sarkari yojana',
            'loan', 'insurance', 'crop insurance', 'pm-fasal', 'pradhan mantri',
            'सरकारी योजना', 'योजना', 'सब्सिडी', 'प्रधानमंत्री', 'पीएम किसान', 'बीमा',
            'किसान क्रेडिट', 'ऋण', 'कर्ज', 'शासकीय योजना', 'शेतकरी योजना',
            'ਯੋਜਨਾ', 'ਸਬਸਿਡੀ', 'ਸਰਕਾਰੀ',
        ]
        return any(keyword in msg for keyword in scheme_keywords)
    
    def is_soil_query(self, message: str) -> bool:
        """Check if user is asking about soil health or fertilizer dosage"""
        msg = (message or "").lower().strip()
        soil_keywords = [
            'soil health', 'soil test', 'npk', 'fertilizer dose', 'fertilizer quantity',
            'how much urea', 'how much dap', 'fertilizer calculator', 'soil problem',
            'yellow leaves', 'nutrient deficiency', 'soil analysis', 'soil report',
            'मिट्टी जांच', 'मिट्टी स्वास्थ्य', 'खाद मात्रा', 'कितना यूरिया', 'कितना डीएपी',
            'पीले पत्ते', 'माती तपासणी', 'खत मात्रा',
        ]
        return any(keyword in msg for keyword in soil_keywords)
    
    def is_economics_query(self, message: str) -> bool:
        """Check if user is asking about farm economics / profit calculation"""
        msg = (message or "").lower().strip()
        econ_keywords = [
            'profit', 'loss', 'cost of cultivation', 'roi', 'return on investment',
            'how much earn', 'how much profit', 'farming profit', 'crop economics',
            'breakeven', 'cultivation cost', 'input cost', 'farm budget',
            'लाभ', 'नुकसान', 'खेती लागत', 'कितना कमा', 'मुनाफा', 'खर्च',
            'उत्पन्न खर्च', 'नफा', 'तोटा', 'लागत', 'शेती खर्च',
        ]
        return any(keyword in msg for keyword in econ_keywords)
    
    def _extract_location(self, message: str) -> str:
        """Extract location from user message, default to common Indian farming regions"""
        msg = message.lower()
        
        # Common Indian cities and farming regions
        indian_locations = {
            'delhi': 'Delhi,IN', 'mumbai': 'Mumbai,IN', 'pune': 'Pune,IN', 
            'bangalore': 'Bangalore,IN', 'hyderabad': 'Hyderabad,IN',
            'chennai': 'Chennai,IN', 'kolkata': 'Kolkata,IN', 'ahmedabad': 'Ahmedabad,IN',
            'jaipur': 'Jaipur,IN', 'lucknow': 'Lucknow,IN', 'chandigarh': 'Chandigarh,IN',
            'bhopal': 'Bhopal,IN', 'indore': 'Indore,IN', 'nagpur': 'Nagpur,IN',
            'patna': 'Patna,IN', 'ludhiana': 'Ludhiana,IN', 'amritsar': 'Amritsar,IN',
            'punjab': 'Punjab,IN', 'haryana': 'Haryana,IN', 'maharashtra': 'Maharashtra,IN',
            'gujarat': 'Gujarat,IN', 'rajasthan': 'Rajasthan,IN', 'karnataka': 'Karnataka,IN',
            'tamil nadu': 'Tamil Nadu,IN', 'kerala': 'Kerala,IN', 'west bengal': 'West Bengal,IN',
            'uttar pradesh': 'Uttar Pradesh,IN', 'madhya pradesh': 'Madhya Pradesh,IN',
        }
        
        # Search for location in message
        for location_key, location_value in indian_locations.items():
            if location_key in msg:
                return location_value
        
        # Default to Delhi if no location specified
        return 'Delhi,IN'
    
    def _get_weather_data(self, location: str) -> dict:
        """Fetch weather data from OpenWeather API with caching"""
        if not OPENWEATHER_API_KEY or OPENWEATHER_API_KEY == '':
            print(" OpenWeather API key not configured")
            return None
        
        # Check cache first
        cached = get_cached_weather(location)
        if cached:
            return cached
        
        print(f" Using OpenWeather API key: {OPENWEATHER_API_KEY[:10]}...")
        
        try:
            # Check if location is coordinates (lat,lon format)
            if ',' in location and all(part.replace('.', '').replace('-', '').isdigit() for part in location.split(',')):
                # Location is coordinates
                lat, lon = location.split(',')
                print(f" Detected coordinates: lat={lat}, lon={lon}")
                
                # Use lat/lon parameters for OpenWeather API
                current_url = f"http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric"
                forecast_url = f"http://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric"
            else:
                # Location is city name
                print(f" Using city name: {location}")
                current_url = f"http://api.openweathermap.org/data/2.5/weather?q={location}&appid={OPENWEATHER_API_KEY}&units=metric"
                forecast_url = f"http://api.openweathermap.org/data/2.5/forecast?q={location}&appid={OPENWEATHER_API_KEY}&units=metric"
            
            print(f" Calling OpenWeather API: {current_url.replace(OPENWEATHER_API_KEY, 'API_KEY')}")
            
            current_response = requests.get(current_url, timeout=15)
            print(f" Current weather status code: {current_response.status_code}")
            
            current_data = current_response.json()
            
            if current_response.status_code != 200:
                print(f" OpenWeather API error: {current_data.get('message', 'Unknown error')}")
                return None
            
            # Try to get forecast, but don't fail if it times out
            forecast_data = None
            try:
                forecast_response = requests.get(forecast_url, timeout=15)
                print(f" Forecast status code: {forecast_response.status_code}")
                
                if forecast_response.status_code == 200:
                    forecast_data = forecast_response.json()
                else:
                    print(f" Forecast API error: {forecast_response.json().get('message', 'Unknown error')}")
                    # Continue with current weather only
            except requests.exceptions.Timeout:
                print(" Forecast API timeout - continuing with current weather only")
            except Exception as e:
                print(f" Forecast API error: {e} - continuing with current weather only")
            
            result = {
                'current': current_data,
                'forecast': forecast_data or {'list': []}  # Empty forecast if unavailable
            }
            
            # Cache the result
            cache_weather(location, result)
            
            print(" Weather data fetched successfully!")
            return result
                
        except requests.exceptions.Timeout:
            print(" OpenWeather API timeout")
            return None
        except requests.exceptions.RequestException as e:
            print(f" Network error fetching weather data: {e}")
            return None
        except Exception as e:
            print(f" Error fetching weather data: {e}")
            return None
    
    def _format_weather_response(self, weather_data: dict, location: str, language: str) -> str:
        """Format weather data into farmer-friendly response"""
        if not weather_data:
            return None
        
        try:
            current = weather_data['current']
            forecast = weather_data['forecast']
            
            # Extract current weather details
            temp = current['main']['temp']
            feels_like = current['main']['feels_like']
            humidity = current['main']['humidity']
            description = current['weather'][0]['description']
            wind_speed = current['wind']['speed']
            
            # Check for rain in current and forecast
            rain_now = 'rain' in current.get('weather', [{}])[0].get('main', '').lower()
            
            # Analyze forecast for rain prediction
            rain_forecast = []
            for item in forecast.get('list', [])[:8]:  # Next 24 hours (8 x 3-hour intervals)
                if 'rain' in item.get('weather', [{}])[0].get('main', '').lower():
                    time_str = item['dt_txt']
                    rain_forecast.append(time_str)
            
            # Build response based on language
            if language == 'hi':
                response = f"🌤️ **{location} के लिए मौसम अपडेट**\n\n"
                response += f"**वर्तमान स्थिति:**\n"
                response += f"• तापमान: {temp}°C (महसूस होता है: {feels_like}°C)\n"
                response += f"• मौसम: {description}\n"
                response += f"• आर्द्रता: {humidity}%\n"
                response += f"• हवा की गति: {wind_speed} m/s\n\n"
                
                if rain_now:
                    response += "🌧️ **अभी बारिश हो रही है!**\n\n"
                
                if rain_forecast:
                    response += f"🌧️ **बारिश का पूर्वानुमान:** अगले 24 घंटों में {len(rain_forecast)} बार बारिश की संभावना\n\n"
                else:
                    response += "☀️ **बारिश का पूर्वानुमान:** अगले 24 घंटों में बारिश की संभावना नहीं\n\n"
                
                response += "**किसानों के लिए सलाह:**\n"
                if rain_forecast:
                    response += "• सिंचाई रोक दें\n• खड़ी फसल में जल निकासी सुनिश्चित करें\n• कटाई को टाल दें"
                else:
                    response += "• सिंचाई की योजना बनाएं\n• मौसम के अनुसार खेती का काम करें"
                    
            elif language == 'mr':
                response = f"🌤️ **{location} साठी हवामान अद्यतन**\n\n"
                response += f"**सध्याची स्थिती:**\n"
                response += f"• तापमान: {temp}°C (वाटते: {feels_like}°C)\n"
                response += f"• हवामान: {description}\n"
                response += f"• आर्द्रता: {humidity}%\n"
                response += f"• वाऱ्याचा वेग: {wind_speed} m/s\n\n"
                
                if rain_now:
                    response += "🌧️ **आत्ता पाऊस पडत आहे!**\n\n"
                
                if rain_forecast:
                    response += f"🌧️ **पावसाचा अंदाज:** पुढील 24 तासांत {len(rain_forecast)} वेळा पाऊस पडण्याची शक्यता\n\n"
                else:
                    response += "☀️ **पावसाचा अंदाज:** पुढील 24 तासांत पाऊस पडण्याची शक्यता नाही\n\n"
                
                response += "**शेतकऱ्यांसाठी सल्ला:**\n"
                if rain_forecast:
                    response += "• पाणी देणे थांबवा\n• शेतात पाणी निचरा सुनिश्चित करा\n• कापणी पुढे ढकला"
                else:
                    response += "• पाणी देण्याची योजना करा\n• हवामानानुसार शेती कामे करा"
                    
            else:  # English
                response = f"🌤️ **Weather Update for {location}**\n\n"
                response += f"**Current Conditions:**\n"
                response += f"• Temperature: {temp}°C (Feels like: {feels_like}°C)\n"
                response += f"• Weather: {description.capitalize()}\n"
                response += f"• Humidity: {humidity}%\n"
                response += f"• Wind Speed: {wind_speed} m/s\n\n"
                
                if rain_now:
                    response += "🌧️ **It's currently raining!**\n\n"
                
                if rain_forecast:
                    response += f"🌧️ **Rain Forecast:** Expected rain {len(rain_forecast)} times in the next 24 hours\n\n"
                else:
                    response += "☀️ **Rain Forecast:** No rain expected in the next 24 hours\n\n"
                
                response += "**Farming Advice:**\n"
                if rain_forecast:
                    response += "• Stop irrigation\n• Ensure proper drainage in fields\n• Postpone harvesting if planned"
                else:
                    response += "• Plan irrigation schedule\n• Continue farming activities as per weather"
            
            return response
            
        except Exception as e:
            print(f" Error formatting weather response: {e}")
            return None
    
    def _get_weather_response(self, message: str, language: str = 'en') -> str:
        """Get weather forecast and provide farming advice"""
        # Extract location from message
        location = self._extract_location(message)
        print(f"🌍 Detected location: {location}")
        
        # Fetch weather data
        weather_data = self._get_weather_data(location)
        
        if weather_data:
            # Format weather response
            formatted_response = self._format_weather_response(weather_data, location, language)
            
            if formatted_response:
                print(" Returning OpenWeather API response")
                return formatted_response
            else:
                print(" Failed to format weather response")
        else:
            print(" Weather data fetch failed")
        
        # Fallback if weather API fails - use Gemini
        print(" Weather API unavailable, using Gemini fallback")
        
        language_names = {'en': 'English', 'hi': 'Hindi', 'mr': 'Marathi', 'pa': 'Punjabi', 'ml': 'Malayalam', 'ta': 'Tamil', 'te': 'Telugu', 'kn': 'Kannada'}
        lang_name = language_names.get(language, 'English')
        
        weather_prompt = f"""You are an agricultural advisor. The farmer is asking about weather: "{message}"

Provide a concise response (max 3 short paragraphs) with:
1. Current typical weather for Delhi/the region in this season (November)
2. What farmers should expect in the coming days/week
3. 2-3 specific farming actions to take now

Respond in {lang_name} with practical, actionable advice. Keep it brief and focused."""

        try:
            if gemini_model is not None:
                response = gemini_model.generate_content(weather_prompt)
                if response and getattr(response, 'text', None):
                    return response.text.strip()
            
            if GEMINI_API_KEY and GEMINI_API_KEY != '':
                return call_gemini_rest(weather_prompt, language)
                
        except Exception as e:
            print(f" Error in weather fallback: {e}")
        
        # Final fallback message
        error_messages = {
            'en': "I apologize, but I cannot access weather information right now. Please check a weather website or app for current conditions in your area.",
            'hi': "मुझे खेद है, लेकिन मैं अभी मौसम की जानकारी प्राप्त नहीं कर सकता। कृपया अपने क्षेत्र में वर्तमान स्थिति के लिए मौसम वेबसाइट या ऐप देखें।",
            'mr': "मला माफ करा, पण मी आत्ता हवामानाची माहिती मिळवू शकत नाही. कृपया तुमच्या क्षेत्रातील सध्याच्या परिस्थितीसाठी हवामान वेबसाइट किंवा अॅप पहा.",
        }
        return error_messages.get(language, error_messages['en'])
    
    def _get_crop_recommendation_response(self, message: str, language: str = 'en', location: str = None) -> str:
        """Get crop recommendations based on current weather and location"""
        # Extract location from message or use provided location
        if not location:
            location = self._extract_location(message)
        
        print(f"🌾 Getting crop recommendations for location: {location}")
        
        # Fetch current weather data
        weather_data = self._get_weather_data(location)
        
        language_names = {'en': 'English', 'hi': 'Hindi', 'mr': 'Marathi', 'pa': 'Punjabi', 'ml': 'Malayalam', 'ta': 'Tamil', 'te': 'Telugu', 'kn': 'Kannada'}
        lang_name = language_names.get(language, 'English')
        
        if weather_data:
            current = weather_data['current']
            forecast = weather_data['forecast']
            
            # Extract weather information
            temp = current['main']['temp']
            humidity = current['main']['humidity']
            city = current['name']
            description = current['weather'][0]['description']
            
            # Check for upcoming rain
            rain_forecast = []
            for item in forecast.get('list', [])[:8]:  # Next 24 hours
                if 'rain' in item.get('weather', [{}])[0].get('main', '').lower():
                    rain_forecast.append(item['dt_txt'])
            
            has_rain = len(rain_forecast) > 0
            
            # Create detailed prompt for Gemini with weather context
            crop_prompt = f"""You are an expert agricultural advisor providing crop recommendations based on current weather and location.

**Current Location**: {city}
**Current Weather**: {description}
**Temperature**: {temp}°C
**Humidity**: {humidity}%
**Rain Expected**: {'Yes - in next 24 hours' if has_rain else 'No rain expected'}
**Current Season**: November (Post-monsoon/Winter season in India)

**Farmer's Question**: "{message}"

Provide comprehensive crop recommendations that include:

1. **Best Crops for Current Conditions** (3-5 crops)
   - List crops that are ideal for the current temperature, humidity, and season
   - Specify why each crop is suitable

2. **Soil Requirements**
   - What soil type is best for these crops
   - Any soil preparation needed

3. **Weather Considerations**
   - How current weather affects planting decisions
   - Best time to plant (specific dates/weeks if possible)

4. **Practical Growing Tips**
   - Water requirements
   - Expected harvest time
   - Any precautions based on current conditions

5. **Alternative Options**
   - 2-3 backup crops if weather changes
   - Short-duration crops for quick harvest

**Important Guidelines**:
- MUST write ENTIRE response in {lang_name} language ONLY
- Focus on crops suitable for Indian climate and farming
- Give specific, actionable advice
- Consider the current season (November)
- Mention any government schemes for these crops if relevant
- Keep it practical and farmer-friendly

Provide detailed crop recommendations in {lang_name}:"""
            
            try:
                # Use Gemini API for crop recommendations
                if gemini_model is not None:
                    try:
                        response = gemini_model.generate_content(crop_prompt)
                        if response and getattr(response, 'text', None):
                            print(f"Got crop recommendations from Gemini")
                            return response.text.strip()
                    except Exception as e:
                        print(f"Error using Gemini model: {e}")
                
                # Try REST API fallback
                if GEMINI_API_KEY and GEMINI_API_KEY != '':
                    try:
                        rest_response = call_gemini_rest(crop_prompt, language)
                        print(f"Got crop recommendations from Gemini REST API")
                        return rest_response
                    except Exception as e:
                        print(f"Gemini REST call failed: {e}")
                
            except Exception as e:
                print(f"Error getting crop recommendations: {e}")
        
        # Fallback if weather data unavailable - still provide recommendations
        print("Weather data unavailable, using general recommendations")
        
        fallback_prompt = f"""You are an agricultural advisor. The farmer is asking: "{message}"

Provide crop recommendations for November season in India (post-monsoon/winter):

1. **Best Crops to Plant Now** (4-5 crops)
   - Why each is suitable for November
   - Expected harvest time

2. **Soil & Water Requirements**
   - Soil type needed
   - Irrigation requirements

3. **Growing Tips**
   - Planting method
   - Care instructions
   - Common problems to avoid

Write ENTIRE response in {lang_name} language ONLY. Be specific and practical."""
        
        try:
            if gemini_model is not None:
                response = gemini_model.generate_content(fallback_prompt)
                if response and getattr(response, 'text', None):
                    return response.text.strip()
            
            if GEMINI_API_KEY and GEMINI_API_KEY != '':
                return call_gemini_rest(fallback_prompt, language)
                
        except Exception as e:
            print(f"Error in crop recommendation fallback: {e}")
        
        # Final fallback message
        error_messages = {
            'en': "I apologize, but I'm having trouble generating crop recommendations right now. For November season in India, consider: Wheat, Mustard, Chickpea (Chana), Peas, and Potato. These are typically good winter crops. Please ask me for specific details about any of these crops.",
            'hi': "मुझे खेद है, लेकिन मुझे अभी फसल सिफारिशें देने में समस्या हो रही है। भारत में नवंबर के मौसम के लिए विचार करें: गेहूं, सरसों, चना, मटर, और आलू। ये आमतौर पर अच्छी शीतकालीन फसलें हैं। कृपया मुझसे इनमें से किसी भी फसल के बारे में विशिष्ट विवरण पूछें।",
            'mr': "मला माफ करा, पण मला आत्ता पीक शिफारशी देण्यात अडचण येत आहे। भारतात नोव्हेंबर हंगामासाठी विचार करा: गहू, मोहरी, हरभरा, वाटाणे आणि बटाटा. ह्या सामान्यतः चांगल्या हिवाळी पिके आहेत. कृपया मला यापैकी कोणत्याही पिकाबद्दल विशिष्ट तपशील विचारा.",
        }
        return error_messages.get(language, error_messages['en'])
    
    def get_out_of_domain_response(self, language: str = 'en') -> str:
        """Return a polite message for non-farming queries"""
        responses = {
            'en': (
                "I apologize, but I'm specifically designed to assist with farming and agricultural questions only. "
                "I can help you with:\n"
                "• Crop diseases and pest management\n"
                "• Fertilizers and soil health\n"
                "• Irrigation and water management\n"
                "• Weather and climate advice\n"
                "• Market prices and farming economics\n"
                "• Livestock management\n\n"
                "Please ask me a farming-related question, and I'll be happy to help!"
            ),
            'hi': (
                "मुझे खेद है, लेकिन मैं विशेष रूप से केवल खेती और कृषि संबंधी प्रश्नों में सहायता के लिए डिज़ाइन किया गया हूं।\n"
                "मैं आपकी इनमें मदद कर सकता हूं:\n"
                "• फसल रोग और कीट प्रबंधन\n"
                "• उर्वरक और मिट्टी स्वास्थ्य\n"
                "• सिंचाई और जल प्रबंधन\n"
                "• मौसम और जलवायु सलाह\n"
                "• बाजार मूल्य और खेती अर्थशास्त्र\n"
                "• पशुधन प्रबंधन\n\n"
                "कृपया मुझसे खेती से संबंधित प्रश्न पूछें, और मुझे आपकी मदद करने में खुशी होगी!"
            ),
            'mr': (
                "मला माफ करा, परंतु मी विशेषतः केवळ शेती आणि कृषी प्रश्नांसाठी मदत करण्यासाठी डिझाइन केलेले आहे।\n"
                "मी तुम्हाला यामध्ये मदत करू शकतो:\n"
                "• पीक रोग आणि कीटक व्यवस्थापन\n"
                "• खते आणि माती आरोग्य\n"
                "• सिंचन आणि पाणी व्यवस्थापन\n"
                "• हवामान आणि हवामान सल्ला\n"
                "• बाजार किंमती आणि शेती अर्थशास्त्र\n"
                "• पशुधन व्यवस्थापन\n\n"
                "कृपया मला शेतीशी संबंधित प्रश्न विचारा आणि मला तुम्हाला मदत करण्यात आनंद होईल!"
            ),
            'pa': (
                "ਮੈਨੂੰ ਮਾਫ਼ ਕਰਨਾ, ਪਰ ਮੈਂ ਖਾਸ ਤੌਰ 'ਤੇ ਸਿਰਫ਼ ਖੇਤੀਬਾੜੀ ਅਤੇ ਖੇਤੀਬਾੜੀ ਸਵਾਲਾਂ ਵਿੱਚ ਸਹਾਇਤਾ ਲਈ ਤਿਆਰ ਕੀਤਾ ਗਿਆ ਹਾਂ।\n"
                "ਮੈਂ ਇਹਨਾਂ ਵਿੱਚ ਤੁਹਾਡੀ ਮਦਦ ਕਰ ਸਕਦਾ ਹਾਂ:\n"
                "• ਫਸਲ ਰੋਗ ਅਤੇ ਕੀਟ ਪ੍ਰਬੰਧਨ\n"
                "• ਖਾਦ ਅਤੇ ਮਿੱਟੀ ਦੀ ਸਿਹਤ\n"
                "• ਸਿੰਚਾਈ ਅਤੇ ਪਾਣੀ ਪ੍ਰਬੰਧਨ\n"
                "• ਮੌਸਮ ਅਤੇ ਜਲਵਾਯੂ ਸਲਾਹ\n"
                "• ਮਾਰਕੀਟ ਕੀਮਤਾਂ ਅਤੇ ਖੇਤੀਬਾੜੀ ਅਰਥਸ਼ਾਸਤਰ\n"
                "• ਪਸ਼ੂ ਪ੍ਰਬੰਧਨ\n\n"
                "ਕਿਰਪਾ ਕਰਕੇ ਮੈਨੂੰ ਖੇਤੀਬਾੜੀ ਨਾਲ ਸਬੰਧਤ ਸਵਾਲ ਪੁੱਛੋ, ਅਤੇ ਮੈਨੂੰ ਮਦਦ ਕਰਨ ਵਿੱਚ ਖੁਸ਼ੀ ਹੋਵੇਗੀ!"
            ),
            'ml': (
                "ക്ഷമിക്കണം, പക്ഷേ ഞാൻ പ്രത്യേകമായി കൃഷിയും കാർഷിക ചോദ്യങ്ങളും മാത്രം സഹായിക്കാൻ രൂപകൽപ്പന ചെയ്തിട്ടുള്ളതാണ്।\n"
                "എനിക്ക് ഇവയിൽ നിങ്ങളെ സഹായിക്കാൻ കഴിയും:\n"
                "• വിള രോഗങ്ങളും കീടനിയന്ത്രണവും\n"
                "• രാസവളങ്ങളും മണ്ണ് ആരോഗ്യവും\n"
                "• ജലസേചനവും ജല പരിപാലനവും\n"
                "• കാലാവസ്ഥ ഉപദേശം\n"
                "• വിപണി വിലകളും കാർഷിക സാമ്പത്തികശാസ്ത്രവും\n"
                "• കന്നുകാലി പരിപാലനം\n\n"
                "ദയവായി എന്നോട് കൃഷിയുമായി ബന്ധപ്പെട്ട ചോദ്യം ചോദിക്കൂ, എനിക്ക് സഹായിക്കാൻ സന്തോഷമുണ്ട്!"
            )
        }
        return responses.get(language, responses['en'])
    
    def get_farming_response(self, message: str, language: str = 'en') -> str:
        """Generate a farming-specific response.

        First checks if query is farming-related. If not, returns out-of-domain message.
        If yes, checks if it's a weather query, crop recommendation, update query, or regular query.
        Otherwise, calls Gemini API directly (no fallback).
        """
        # Check if the message is farming-related
        if not self.is_farming_related(message):
            print(f" Non-farming query detected: {message[:50]}...")
            return self.get_out_of_domain_response(language)
        
        # Check if this is a crop recommendation query (check before weather query)
        if self.is_crop_recommendation_query(message):
            print(f"Crop recommendation query detected: {message[:50]}...")
            return self._get_crop_recommendation_response(message, language)
        
        # Check if this is a weather query
        if self.is_weather_query(message):
            print(f"Weather query detected: {message[:50]}...")
            return self._get_weather_response(message, language)
        
        # Check if this is an update/news query
        if self.is_update_query(message):
            print(f"Update query detected: {message[:50]}...")
            return self._get_update_response(message, language)
        
        # Check if this is a mandi/price query → enrich with real data
        if self.is_mandi_price_query(message) and mandi_service:
            print(f"💰 Mandi price query detected: {message[:50]}...")
            return self._get_mandi_enriched_response(message, language)
        
        # Check if this is a government scheme query → enrich with scheme data
        if self.is_scheme_query(message) and scheme_service:
            print(f"🏛️ Scheme query detected: {message[:50]}...")
            return self._get_scheme_enriched_response(message, language)
        
        # Check if this is a soil health query → enrich with soil data
        if self.is_soil_query(message) and soil_service:
            print(f"🧪 Soil query detected: {message[:50]}...")
            return self._get_soil_enriched_response(message, language)
        
        # Check if this is a farm economics query → enrich with economics data
        if self.is_economics_query(message) and economics_service:
            print(f"📊 Economics query detected: {message[:50]}...")
            return self._get_economics_enriched_response(message, language)
        
        print(f"Farming query detected: {message[:50]}... Calling API...")
        
        # For farming queries, ALWAYS call the API (no local fallback)
        try:
            response = self._get_gemini_response(message, language)
            if response and response.strip() != "":
                return response
        except Exception as e:
            # If API fails, return error message instead of fallback
            print(f" API Error: {e}")
            error_messages = {
                'en': "I'm sorry, I'm having trouble connecting right now. Please try again in a moment. 🙏",
                'hi': "क्षमा करें, अभी कनेक्शन में समस्या हो रही है। कृपया थोड़ी देर में पुन: प्रयास करें। 🙏",
                'mr': "माफ करा, सध्या कनेक्शनमध्ये अडचण आहे. कृपया थोड्या वेळाने पुन्हा प्रयत्न करा. 🙏",
                'pa': "ਮਾਫ਼ ਕਰਨਾ, ਹੁਣ ਕਨੈਕਸ਼ਨ ਵਿੱਚ ਸਮੱਸਿਆ ਹੈ। ਕਿਰਪਾ ਕਰਕੇ ਥੋੜ੍ਹੀ ਦੇਰ ਬਾਅਦ ਦੁਬਾਰਾ ਕੋਸ਼ਿਸ਼ ਕਰੋ। 🙏",
                'ml': "ക്ഷമിക്കണം, ഇപ്പോൾ കണക്ഷനിൽ പ്രശ്നമുണ്ട്. ഒരു നിമിഷത്തിനുള്ളിൽ വീണ്ടും ശ്രമിക്കുക. 🙏",
                'ta': "மன்னிக்கவும், இப்போது இணைப்பில் சிக்கல் உள்ளது. சிறிது நேரம் கழித்து மீண்டும் முயற்சிக்கவும். 🙏",
                'te': "క్షమించండి, ప్రస్తుతం కనెక్షన్‌లో సమస్య ఉంది. దయచేసి కొన్ని క్షణాల్లో మళ్ళీ ప్రయత్నించండి. 🙏",
                'kn': "ಕ್ಷಮಿಸಿ, ಈಗ ಸಂಪರ್ಕದಲ್ಲಿ ಸಮಸ್ಯೆ ಇದೆ. ದಯವಿಟ್ಟು ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ. 🙏"
            }
            return error_messages.get(language, error_messages['en'])

    # ---- Service-Enriched Response Methods ----

    def _get_mandi_enriched_response(self, message: str, language: str = 'en') -> str:
        """Get mandi price info from farmer_services + Gemini for natural language"""
        commodity = mandi_service.detect_commodity_in_message(message)
        data_context = ""
        if commodity:
            prices = mandi_service.get_mandi_prices(commodity)
            data_context = f"Real data for {commodity}: {json.dumps(prices, ensure_ascii=False, default=str)}"
        else:
            msp = mandi_service.get_msp_data()
            data_context = f"MSP Data: {json.dumps(msp, ensure_ascii=False, default=str)}"

        language_names = {'en': 'English', 'hi': 'Hindi', 'mr': 'Marathi', 'pa': 'Punjabi', 'ml': 'Malayalam', 'ta': 'Tamil', 'te': 'Telugu', 'kn': 'Kannada'}
        lang_name = language_names.get(language, 'English')
        prompt = f"""You are an expert agricultural market advisor. The farmer asks: "{message}"

Here is REAL market data to use in your response:
{data_context}

Provide a helpful response in {lang_name} that includes:
- Current market prices and MSP comparison
- Whether to sell now or wait
- Which mandi offers the best price
- Practical selling advice

Also mention: For live Mandi prices dashboard, visit the Farmer Dashboard.
Write ENTIRELY in {lang_name}. Keep it concise and actionable."""
        try:
            return self._get_gemini_response(prompt, language)
        except:
            return data_context

    def _get_scheme_enriched_response(self, message: str, language: str = 'en') -> str:
        """Get scheme info from farmer_services + Gemini for natural language"""
        schemes = scheme_service.get_all_schemes()
        scheme_summary = []
        for s in schemes[:5]:
            scheme_summary.append(f"{s.get('name', {}).get('en', s.get('id', ''))}: {s.get('benefit', '')}")

        language_names = {'en': 'English', 'hi': 'Hindi', 'mr': 'Marathi', 'pa': 'Punjabi', 'ml': 'Malayalam', 'ta': 'Tamil', 'te': 'Telugu', 'kn': 'Kannada'}
        lang_name = language_names.get(language, 'English')
        prompt = f"""You are an expert agricultural advisor helping farmers with government schemes. The farmer asks: "{message}"

Here are relevant government schemes data:
{chr(10).join(scheme_summary)}

Total schemes available: {len(schemes)}

Provide a helpful response in {lang_name} that:
- Lists the most relevant schemes for their query
- Includes eligibility criteria and benefits
- Explains how to apply (step by step)
- Mentions required documents
- Also mention: For full scheme details, visit the Farmer Dashboard's Government Schemes tab.
Write ENTIRELY in {lang_name}. Keep it practical."""
        try:
            return self._get_gemini_response(prompt, language)
        except:
            return "\n".join(scheme_summary)

    def _get_soil_enriched_response(self, message: str, language: str = 'en') -> str:
        """Get soil health info from farmer_services + Gemini"""
        analysis = soil_service.analyze_soil_symptoms(message)

        language_names = {'en': 'English', 'hi': 'Hindi', 'mr': 'Marathi', 'pa': 'Punjabi', 'ml': 'Malayalam', 'ta': 'Tamil', 'te': 'Telugu', 'kn': 'Kannada'}
        lang_name = language_names.get(language, 'English')
        prompt = f"""You are an expert soil scientist and agricultural advisor. The farmer asks: "{message}"

Soil analysis data:
- Detected issues: {analysis.get('detected_issues', [])}
- Recommendations: {analysis.get('recommendations', [])}
- General advice: {analysis.get('general_advice', [])}

Provide a detailed response in {lang_name} with:
- What the soil problem likely is
- Specific fertilizer quantities (per hectare/acre)
- Organic and chemical solutions
- Prevention tips
- Also mention: Use the Farmer Dashboard's Soil Health tab for NPK calculator.
Write ENTIRELY in {lang_name}. Be specific with quantities."""
        try:
            return self._get_gemini_response(prompt, language)
        except:
            return json.dumps(analysis, ensure_ascii=False)

    def _get_economics_enriched_response(self, message: str, language: str = 'en') -> str:
        """Get farm economics from farmer_services + Gemini"""
        crop = None
        for c in ['wheat', 'rice', 'cotton', 'soybean', 'potato', 'onion', 'tomato', 'maize', 'sugarcane', 'mustard', 'chana']:
            if c in message.lower():
                crop = c
                break

        data_context = ""
        if crop:
            econ = economics_service.calculate_economics(crop, 1.0)
            data_context = f"Economics for {crop} (1 hectare): {json.dumps(econ, ensure_ascii=False, default=str)}"
        else:
            comparison = economics_service.compare_crops(['wheat', 'rice', 'cotton', 'soybean', 'potato'])
            data_context = f"Crop comparison: Best={comparison.get('best_crop')}, ROI={comparison.get('best_roi')}%"

        language_names = {'en': 'English', 'hi': 'Hindi', 'mr': 'Marathi', 'pa': 'Punjabi', 'ml': 'Malayalam', 'ta': 'Tamil', 'te': 'Telugu', 'kn': 'Kannada'}
        lang_name = language_names.get(language, 'English')
        prompt = f"""You are an expert farm economics advisor. The farmer asks: "{message}"

Real economics data:
{data_context}

Provide a helpful response in {lang_name} with:
- Input cost breakdown
- Expected yield and revenue
- Net profit and ROI percentage
- Practical advice to increase profit
- Also mention: Use the Farmer Dashboard's Farm Economics tab for detailed calculator.
Write ENTIRELY in {lang_name}. Use ₹ for currency."""
        try:
            return self._get_gemini_response(prompt, language)
        except:
            return data_context

    def _get_update_response(self, message: str, language: str = 'en') -> str:
        """Generate response for update/news queries with special prompt"""
        language_names = {
            'en': 'English',
            'hi': 'Hindi', 
            'mr': 'Marathi',
            'pa': 'Punjabi',
            'ml': 'Malayalam',
            'ta': 'Tamil',
            'te': 'Telugu',
            'kn': 'Kannada'
        }
        lang_name = language_names.get(language, 'English')

        update_prompt = f"""You are an expert agricultural advisor providing current updates and news about farming. The farmer is asking: "{message}"

Provide comprehensive, up-to-date information about the topic they're asking about. Include:

1. **Current Status/Updates**: What's happening now in this area (current season considerations, recent developments, government schemes, etc.)

2. **Recent Trends**: Any new practices, technologies, or methods that farmers should know about

3. **Practical Recommendations**: What farmers should do NOW based on current conditions (season-specific advice, timing considerations, etc.)

4. **Important Alerts**: Any warnings, precautions, or time-sensitive information relevant to the topic

Guidelines:
- CRITICAL: Write your ENTIRE response in {lang_name} language ONLY. Every single word must be in {lang_name}.
- Use simple, farmer-friendly language
- Focus on actionable, current information
- Include approximate timings and seasons where relevant
- Mention government schemes or support programs if applicable
- Keep it practical and relevant to Indian farming conditions
- Structure with clear sections/bullet points for easy reading

Provide the update/news in {lang_name} (remember: ONLY use {lang_name} language):"""

        try:
            # Use Gemini API for update response
            if gemini_model is not None:
                try:
                    response = gemini_model.generate_content(update_prompt)
                    if response and getattr(response, 'text', None):
                        print(f"Got update response from native Gemini client")
                        return response.text.strip()
                except Exception as e:
                    print(f" Error using google.generativeai model: {e}")

            # Try REST API fallback
            if GEMINI_API_KEY and GEMINI_API_KEY != '':
                try:
                    print(f" Attempting Gemini REST API for updates...")
                    rest_response = call_gemini_rest(update_prompt, language)
                    print(f" Got update response from Gemini REST API")
                    return rest_response
                except Exception as e:
                    print(f" Gemini REST call failed: {e}")

            # If API fails, return error
            raise RuntimeError("Could not fetch updates")
            
        except Exception as e:
            print(f" Error getting updates: {e}")
            error_messages = {
                'en': "I apologize, but I'm having trouble fetching the latest updates right now. Please try again in a moment.",
                'hi': "मुझे खेद है, लेकिन मुझे अभी नवीनतम अपडेट प्राप्त करने में समस्या हो रही है। कृपया एक क्षण में पुन: प्रयास करें।",
                'mr': "मला माफ करा, पण मला सध्या नवीनतम अद्यतने मिळवण्यात अडचण येत आहे. कृपया थोड्या वेळाने पुन्हा प्रयत्न करा.",
                'pa': "ਮੈਨੂੰ ਮਾਫ਼ ਕਰੋ, ਪਰ ਮੈਨੂੰ ਹੁਣ ਨਵੀਨਤਮ ਅੱਪਡੇਟ ਪ੍ਰਾਪਤ ਕਰਨ ਵਿੱਚ ਮੁਸ਼ਕਲ ਆ ਰਹੀ ਹੈ। ਕਿਰਪਾ ਕਰਕੇ ਇੱਕ ਪਲ ਵਿੱਚ ਦੁਬਾਰਾ ਕੋਸ਼ਿਸ਼ ਕਰੋ।",
                'ml': "ക്ഷമിക്കണം, എന്നാൽ ഏറ്റവും പുതിയ അപ്ഡേറ്റുകൾ നേടുന്നതിൽ എനിക്ക് ഇപ്പോൾ പ്രശ്നമുണ്ട്. ഒരു നിമിഷത്തിനുള്ളിൽ വീണ്ടും ശ്രമിക്കുക."
            }
            return error_messages.get(language, error_messages['en'])

    def _get_gemini_response(self, message: str, language: str = 'en') -> str:
        """Generate response using Gemini API. Tries native client first, then REST fallback."""
        language_names = {
            'en': 'English',
            'hi': 'Hindi', 
            'mr': 'Marathi',
            'pa': 'Punjabi',
            'ml': 'Malayalam',
            'ta': 'Tamil',
            'te': 'Telugu',
            'kn': 'Kannada'
        }
        lang_name = language_names.get(language, 'English')
        
        # Debug: Log the language being used
        print(f"Generating response in {lang_name} (code: {language})")

        # Check if prompt already has its own system instructions (enriched prompts)
        is_custom_prompt = ("You are an expert" in message or "Here is REAL" in message 
                           or "Real data" in message or "Soil analysis" in message
                           or "disease detection" in message or "Disease detected" in message)

        if is_custom_prompt:
            # Use the message as-is (it already has full instructions)
            farming_prompt = message
        else:
            # Apply the expert Indian farmer persona prompt
            farming_prompt = f"""You are "Krishi Mitra" (कृषि मित्र) — a wise, experienced Indian agricultural expert who speaks like a trusted village elder and farming doctor. You have 30+ years of practical farming experience across India.

Your personality:
- Speak warmly and practically, like a knowledgeable farmer friend
- Use simple, easy-to-understand language (avoid heavy technical jargon)
- Give specific, actionable advice with exact quantities, timings, and product names
- Prioritize low-cost, locally available solutions first, then chemical options
- Always consider Indian farming conditions (climate, soil types, available resources)
- Include safety warnings when discussing chemicals/pesticides

Response format:
- Keep responses concise but complete (2-4 short paragraphs + bullet points)
- Use relevant emojis (🌾 🌱 💧 🐄 etc.) to make it visually friendly
- Include a step-by-step action plan when the question involves "how to"
- Mention approximate costs in ₹ where relevant
- Suggest both organic/natural AND chemical solutions when applicable
- Reference Indian seasons (Kharif, Rabi, Zaid) and local crop calendars

CRITICAL: Write your ENTIRE response in {lang_name} language ONLY. Every single word must be in {lang_name}.

Farmer's question: {message}

Provide expert farming advice in {lang_name}:"""

        # Try native Gemini client first
        native_error = None
        if gemini_model is not None:
            try:
                response = gemini_model.generate_content(farming_prompt)
                if response and getattr(response, 'text', None):
                    print(f" Got response from native Gemini client")
                    return response.text.strip()
                else:
                    native_error = "Response had no text content"
                    print(f" Native client returned empty response")
            except Exception as e:
                native_error = str(e)
                print(f" Error using google.generativeai model: {e}")

        # Try REST API as fallback (ALWAYS try this if native failed)
        if GEMINI_API_KEY and GEMINI_API_KEY != '':
            try:
                print(f" Attempting Gemini REST API call...")
                rest_response = call_gemini_rest(farming_prompt, language)
                if rest_response and rest_response.strip():
                    print(f" Got response from Gemini REST API")
                    return rest_response
            except Exception as e:
                print(f" Gemini REST call also failed: {e}")
                raise RuntimeError(f"Both Gemini APIs failed. Native: {native_error}, REST: {e}")

        # If both fail, raise error
        raise RuntimeError(f"Gemini API is not available. Native error: {native_error}")

# Initialize Farming AI
farming_ai = FarmingAI()

# ----------------------
# Chatbot API Routes
# ----------------------

@app.route('/api/chat', methods=['POST'])
def chat():
    """Handle chat messages from users"""
    if 'user_email' not in session:
        return jsonify({'error': 'Authentication required', 'redirect': '/login'}), 401
    
    data = request.get_json()
    message = data.get('message', '').strip()
    user_query = data.get('userQuery', '').strip()  # Original user question
    detected_diseases = data.get('detectedDiseases', [])  # List of detected diseases from ML
    image_count = data.get('imageCount', 0)  # Number of images analyzed
    
    if not message:
        return jsonify({'error': 'Message cannot be empty'}), 400
    
    user_email = session['user_email']
    user_language = get_user_language()
    
    # Get or create active session
    current_session_id = get_active_session()
    
    # Validate session size limit (16MB)
    is_valid, current_size = validate_session_size(current_session_id)
    if not is_valid:
        return jsonify({
            'error': 'Session size limit reached',
            'message': f'This chat has exceeded the {SESSION_SIZE_LIMIT_MB}MB limit ({current_size:.2f}MB). Please start a new chat.',
            'session_full': True,
            'current_size_mb': round(current_size, 2),
            'limit_mb': SESSION_SIZE_LIMIT_MB
        }), 400
    
    # Debug logging
    print(f" Chat request received:")
    print(f"   Session ID: {current_session_id}")
    print(f"   Session size: {current_size:.2f}MB / {SESSION_SIZE_LIMIT_MB}MB")
    print(f"   User query: {user_query}")
    print(f"   Detected diseases: {detected_diseases}")
    print(f"   Image count: {image_count}")
    print(f"   Language: {user_language}")
    
    # If this is an image analysis query with detected diseases, enhance the prompt
    if detected_diseases and len(detected_diseases) > 0:
        # Build a better prompt for Gemini with disease context
        disease_names = ', '.join(detected_diseases)
        
        # Language mapping for better context
        language_names = {
            'en': 'English',
            'hi': 'Hindi (हिंदी)', 
            'mr': 'Marathi (मराठी)',
            'pa': 'Punjabi (ਪੰਜਾਬੀ)',
            'ml': 'Malayalam (മലയാളം)',
            'ta': 'Tamil (தமிழ்)',
            'te': 'Telugu (తెలుగు)',
            'kn': 'Kannada (ಕನ್ನಡ)'
        }
        lang_name = language_names.get(user_language, 'English')
        
        # Build enhanced prompt for Gemini
        if user_query:
            enhanced_message = f"""You are an expert agricultural advisor. A farmer uploaded {image_count} plant image(s) and our disease detection system identified: {disease_names}

The farmer asks: "{user_query}"

Provide a comprehensive response in {lang_name} ONLY with this exact structure:

**रोग की जानकारी** (Disease Information):
[Explain what {disease_names} is, causes, and why it happens - 2-3 sentences]

**लक्षण** (Symptoms):
• [List 3-4 visible symptoms the farmer can check]

**उपचार** (Treatment):

**रासायनिक उपचार** (Chemical):
• [Specific fungicide/pesticide name] - [dose per liter]
• [Application method and timing]

**प्राकृतिक उपचार** (Organic):
• [Natural remedy 1 with preparation method]
• [Natural remedy 2 with preparation method]

**छिड़काव कब करें** (When to spray):
• [Timing instructions - morning/evening, frequency]

**रोकथाम** (Prevention):
• [3-4 prevention tips for future]

**आपके सवाल का जवाब**: 
[Direct answer to: "{user_query}"]

**सावधानी**: [1-2 safety precautions]

IMPORTANT: Write ONLY in {lang_name}. Be specific with product names and quantities. Make it actionable."""
        else:
            # Image only, no question - provide concise response
            enhanced_message = f"""A farmer uploaded plant images. Disease detected: {disease_names}

Provide a concise response in {lang_name} with:

**रोग**: {disease_names}

**त्वरित उपचार** (Quick Treatment):
• [2 immediate actions]
• [1 chemical treatment option]
• [1 organic treatment option]

**रोकथाम**: [2 prevention tips]

Keep it brief but actionable. Write ONLY in {lang_name}."""
        
        print(f" Enhanced prompt created for Gemini")
        print(f"   Disease names: {disease_names}")
        
        # Use enhanced message for AI - but bypass weather/update detection since this is disease analysis
        ai_response = farming_ai._get_gemini_response(enhanced_message, user_language)
    else:
        print(f" No diseases detected, using regular chat")
        # Regular chat message
        ai_response = farming_ai.get_farming_response(message, user_language)
    
    # Save chat history to database
    try:
        # Get UserID from email
        user_query_sql = "SELECT UserID FROM Users WHERE Email = ?"
        user_result = execute_query(user_query_sql, [user_email], fetch=True)
        
        if user_result:
            user_id = user_result[0][0]
            
            # Prepare detected diseases as JSON string
            diseases_json = ', '.join(detected_diseases) if detected_diseases else None
            
            # Insert chat record
            chat_insert_query = """
                INSERT INTO Chats (UserID, UserMessage, AIResponse, Language, HasImages, DetectedDiseases, Timestamp, SessionID)
                VALUES (?, ?, ?, ?, ?, ?, NOW(), ?)
            """
            chat_params = (
                user_id,
                user_query if user_query else message,
                ai_response,
                user_language,
                1 if image_count > 0 else 0,
                diseases_json,
                current_session_id  # Use the active session ID
            )
            execute_query(chat_insert_query, chat_params)
            
            print(f" Chat saved to session: {current_session_id}")
    except Exception as e:
        print(f" Error saving chat: {e}")
        import traceback
        traceback.print_exc()
    
    return jsonify({
        'success': True,
        'response': ai_response,
        'language': user_language
    })

@app.route('/api/chat/history')
def get_chat_history():
    """Get chat history for the current user"""
    if 'user_email' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    
    user_email = session['user_email']
    
    try:
        # Get chat history with SQL query
        query = """
            SELECT c.ID, c.UserMessage, c.AIResponse, c.Language, c.Timestamp
            FROM Chats c
            INNER JOIN Users u ON c.UserID = u.UserID
            WHERE u.Email = ?
            ORDER BY c.Timestamp DESC
            LIMIT 50
        """
        rows = execute_query(query, [user_email], fetch=True)
        
        chat_history = []
        if rows:
            # Reverse to show chronological order (oldest first)
            for row in reversed(rows):
                chat_history.append({
                    'id': str(row[0]),
                    'userMessage': row[1],
                    'aiResponse': row[2],
                    'language': row[3] or 'en',
                    'timestamp': row[4].isoformat() if row[4] else ''
                })
        
        return jsonify({'history': chat_history})
    except Exception as e:
        print(f" Error fetching chat history: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'history': []})

@app.route('/api/chat/clear', methods=['POST'])
def clear_chat_history():
    """Clear chat history for the current user"""
    if 'user_email' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    
    user_email = session['user_email']
    
    try:
        # Delete chat history for this user
        delete_query = """
            DELETE FROM Chats
            WHERE UserID = (SELECT UserID FROM Users WHERE Email = ?)
        """
        result = execute_query(delete_query, [user_email])
        
        if result:
            return jsonify({
                'success': True,
                'message': 'Chat history cleared successfully'
            })
        else:
            return jsonify({'error': 'Failed to clear chat history'}), 500
    except Exception as e:
        print(f" Error clearing chat history: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Failed to clear chat history'}), 500

# ----------------------
# Weather API Routes
# ----------------------

@app.route('/api/weather', methods=['GET'])
def get_weather():
    """Get current weather and forecast for a location"""
    location = request.args.get('location', 'Delhi,IN')
    
    try:
        # Use the FarmingAI weather methods
        weather_data = farming_ai._get_weather_data(location)
        
        if not weather_data:
            return jsonify({
                'error': 'Unable to fetch weather data',
                'message': 'Weather service temporarily unavailable'
            }), 503
        
        current = weather_data['current']
        forecast = weather_data['forecast']
        
        # Extract current weather
        current_weather = {
            'temperature': round(current['main']['temp'], 1),
            'feels_like': round(current['main']['feels_like'], 1),
            'humidity': current['main']['humidity'],
            'wind_speed': round(current['wind']['speed'], 1),
            'description': current['weather'][0]['description'],
            'icon': current['weather'][0]['icon'],
            'city': current['name'],
            'country': current['sys']['country']
        }
        
        # Analyze forecast for rain/snow predictions
        rain_forecast = []
        snow_forecast = []
        hourly_forecast = []
        
        for item in forecast.get('list', [])[:16]:  # Next 48 hours
            forecast_time = item['dt_txt']
            weather_main = item.get('weather', [{}])[0].get('main', '').lower()
            temp = round(item['main']['temp'], 1)
            description = item.get('weather', [{}])[0].get('description', '')
            
            # Check for rain
            if 'rain' in weather_main:
                rain_forecast.append({
                    'time': forecast_time,
                    'description': description,
                    'temp': temp
                })
            
            # Check for snow
            if 'snow' in weather_main:
                snow_forecast.append({
                    'time': forecast_time,
                    'description': description,
                    'temp': temp
                })
            
            # Add to hourly forecast (next 24 hours only)
            if len(hourly_forecast) < 8:
                hourly_forecast.append({
                    'time': forecast_time,
                    'temp': temp,
                    'description': description,
                    'icon': item.get('weather', [{}])[0].get('icon', '')
                })
        
        # Prepare predictions
        predictions = {
            'rain': {
                'expected': len(rain_forecast) > 0,
                'count': len(rain_forecast),
                'times': rain_forecast[:3]  # Show first 3 occurrences
            },
            'snow': {
                'expected': len(snow_forecast) > 0,
                'count': len(snow_forecast),
                'times': snow_forecast[:3]
            }
        }
        
        # Farming advice
        farming_advice = []
        if len(rain_forecast) > 0:
            farming_advice.append("🌧️ Rain expected - stop irrigation and ensure drainage")
        if len(snow_forecast) > 0:
            farming_advice.append("❄️ Snow expected - protect sensitive crops")
        if len(rain_forecast) == 0 and len(snow_forecast) == 0:
            farming_advice.append("☀️ No rain expected - plan irrigation schedule")
        
        if current_weather['temperature'] > 35:
            farming_advice.append("🔥 High temperature - provide shade for crops and livestock")
        elif current_weather['temperature'] < 10:
            farming_advice.append("🥶 Cold weather - protect crops from frost")
        
        return jsonify({
            'success': True,
            'current': current_weather,
            'predictions': predictions,
            'hourly_forecast': hourly_forecast,
            'farming_advice': farming_advice,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        print(f" Error in weather API: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'error': 'Failed to fetch weather',
            'message': str(e)
        }), 500

@app.route('/api/sessions', methods=['GET'])
def get_sessions():
    """Get all chat sessions for the current user"""
    if 'user_email' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    
    user_email = session['user_email']
    
    try:
        sessions = get_user_sessions(user_email)
        active_session = get_active_session()
        
        return jsonify({
            'success': True,
            'sessions': sessions,
            'active_session_id': active_session,
            'session_limit_mb': SESSION_SIZE_LIMIT_MB
        })
    except Exception as e:
        print(f"Error getting sessions: {e}")
        return jsonify({'error': 'Failed to get sessions'}), 500

@app.route('/api/sessions/new', methods=['POST'])
def create_session():
    """Create a new chat session"""
    if 'user_email' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    
    try:
        new_session_id = create_new_session()
        set_active_session(new_session_id)
        
        print(f" New session created: {new_session_id}")
        
        return jsonify({
            'success': True,
            'session_id': new_session_id,
            'message': 'New chat session created'
        })
    except Exception as e:
        print(f"Error creating session: {e}")
        return jsonify({'error': 'Failed to create session'}), 500

@app.route('/api/sessions/<session_id>/activate', methods=['POST'])
def activate_session(session_id):
    """Switch to a different chat session"""
    if 'user_email' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    
    try:
        set_active_session(session_id)
        
        print(f" Switched to session: {session_id}")
        
        return jsonify({
            'success': True,
            'session_id': session_id,
            'message': 'Session activated'
        })
    except Exception as e:
        print(f"Error activating session: {e}")
        return jsonify({'error': 'Failed to activate session'}), 500

@app.route('/api/sessions/<session_id>/messages', methods=['GET'])
def get_session_messages(session_id):
    """Get all messages from a specific session"""
    if 'user_email' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    
    user_email = session['user_email']
    
    try:
        # Get user ID
        user_query = "SELECT UserID FROM Users WHERE Email = ?"
        user_result = execute_query(user_query, [user_email], fetch=True)
        
        if not user_result:
            return jsonify({'error': 'User not found'}), 404
        
        user_id = user_result[0][0]
        
        # Get messages for this session
        messages_query = """
            SELECT ID, UserMessage, AIResponse, Timestamp, HasImages, DetectedDiseases
            FROM Chats
            WHERE UserID = ? AND SessionID = ?
            ORDER BY Timestamp ASC
        """
        messages_result = execute_query(messages_query, [user_id, session_id], fetch=True)
        
        messages = []
        if messages_result:
            for row in messages_result:
                messages.append({
                    'id': row[0],
                    'user_message': row[1],
                    'ai_response': row[2],
                    'timestamp': row[3].isoformat() if row[3] else None,
                    'has_images': bool(row[4]),
                    'detected_diseases': row[5]
                })
        
        return jsonify({
            'success': True,
            'session_id': session_id,
            'messages': messages
        })
    except Exception as e:
        print(f"Error getting session messages: {e}")
        return jsonify({'error': 'Failed to get messages'}), 500

# ----------------------
# Routes
# ----------------------

@app.route('/')
def home():
    user = None
    lang = get_user_language()
    lang_code = convert_language_code(lang)
    
    user = None
    if 'user_email' in session:
        try:
            query = "SELECT UserID, FullName, Email, PreferredLanguage FROM Users WHERE Email = ?"
            result = execute_query(query, [session['user_email']], fetch=True)
            if result:
                user = {
                    '_id': result[0][0],
                    'fullName': result[0][1],
                    'email': result[0][2],
                    'languagePreference': result[0][3]
                }
        except:
            pass
    
    return render_template('index.html', 
                         user=user, 
                         lang=lang_code, 
                         translations=translations.get(lang_code, {}))

@app.route('/weather')
def weather_page():
    """Dedicated weather forecast page"""
    user = None
    if 'user_email' in session:
        try:
            query = "SELECT UserID, FullName, Email, PreferredLanguage FROM Users WHERE Email = ?"
            result = execute_query(query, [session['user_email']], fetch=True)
            if result:
                user = {
                    '_id': result[0][0],
                    'fullName': result[0][1],
                    'email': result[0][2],
                    'languagePreference': result[0][3]
                }
        except:
            pass
    return render_template('weather.html', user=user,
                           lang=get_user_language(),
                           translations=translations.get(get_user_language(), {}))

@app.route('/dashboard')
def dashboard_page():
    """Farmer Dashboard - Mandi prices, schemes, calendar, economics"""
    user = None
    if 'user_email' in session:
        try:
            query = "SELECT UserID, FullName, Email, PreferredLanguage FROM Users WHERE Email = ?"
            result = execute_query(query, [session['user_email']], fetch=True)
            if result:
                user = {
                    '_id': result[0][0],
                    'fullName': result[0][1],
                    'email': result[0][2],
                    'languagePreference': result[0][3]
                }
        except:
            pass
    if not user:
        return redirect(url_for('login'))
    return render_template('dashboard.html', user=user,
                           lang=get_user_language(),
                           translations=translations.get(get_user_language(), {}))

# ----------------------
# Farmer Services API Routes
# ----------------------

def _get_crop_name(crop_key, lang='en'):
    """Get localized crop name from COMMODITY_MAP. Returns only the selected language."""
    if mandi_service and crop_key in mandi_service.COMMODITY_MAP:
        names = mandi_service.COMMODITY_MAP[crop_key]
        return names.get(lang, names.get('en', crop_key.title()))
    return crop_key.title()

@app.route('/api/crop-names', methods=['GET'])
def api_crop_names():
    """Return crop names in the requested language for dynamic dropdowns"""
    lang = request.args.get('lang', session.get('language', 'en'))
    if not mandi_service:
        return jsonify({'success': False, 'error': 'Service unavailable'}), 503
    
    crop_emojis = {
        'wheat': '🌾', 'rice': '🌾', 'onion': '🧅', 'potato': '🥔', 'tomato': '🍅',
        'soybean': '🫘', 'cotton': '🏵️', 'mustard': '🌿', 'chana': '🫘', 'maize': '🌽',
        'bajra': '🌾', 'jowar': '🌾', 'sugarcane': '🌿', 'turmeric': '🟡',
        'garlic': '🧄', 'chilli': '🌶️', 'banana': '🍌', 'mango': '🥭',
    }
    
    crops = {}
    for key, names in mandi_service.COMMODITY_MAP.items():
        localized = names.get(lang, names.get('en', key.title()))
        emoji = crop_emojis.get(key, '🌱')
        crops[key] = {'name': localized, 'emoji': emoji}
    
    return jsonify({'success': True, 'crops': crops, 'lang': lang})

@app.route('/api/mandi/prices', methods=['GET'])
def api_mandi_prices():
    """Get real-time mandi prices for a commodity"""
    if not mandi_service:
        return jsonify({'error': 'Mandi service not available'}), 503
    commodity = request.args.get('commodity', '').strip()
    state = request.args.get('state', '').strip()
    if not commodity:
        return jsonify({'error': 'Commodity parameter is required'}), 400
    try:
        data = mandi_service.get_mandi_prices(commodity, state)
        return jsonify({'success': True, **data})
    except Exception as e:
        print(f"Mandi prices error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/mandi/msp', methods=['GET'])
def api_msp_data():
    """Get MSP (Minimum Support Price) data"""
    if not mandi_service:
        return jsonify({'error': 'Mandi service not available'}), 503
    try:
        data = mandi_service.get_msp_data()
        return jsonify({'success': True, 'msp_data': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schemes', methods=['GET'])
def api_schemes():
    """Get all government schemes for farmers"""
    if not scheme_service:
        return jsonify({'error': 'Scheme service not available'}), 503
    try:
        crop = request.args.get('crop', '')
        land_size = request.args.get('land_size', '')
        if crop or land_size:
            schemes = scheme_service.find_schemes(crop=crop, land_size=land_size)
        else:
            schemes = scheme_service.get_all_schemes()
        return jsonify({'success': True, 'schemes': schemes})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schemes/<scheme_id>', methods=['GET'])
def api_scheme_details(scheme_id):
    """Get details of a specific scheme"""
    if not scheme_service:
        return jsonify({'error': 'Scheme service not available'}), 503
    try:
        scheme = scheme_service.get_scheme_details(scheme_id)
        if scheme:
            return jsonify({'success': True, 'scheme': scheme})
        return jsonify({'success': False, 'error': 'Scheme not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/crop-calendar', methods=['GET'])
def api_crop_calendar():
    """Get crop calendar for a specific month"""
    if not crop_calendar_service:
        return jsonify({'error': 'Calendar service not available'}), 503
    try:
        month = request.args.get('month', type=int)
        data = crop_calendar_service.get_monthly_tasks(month=month)
        return jsonify({'success': True, 'calendar': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/crop-calendar/season', methods=['GET'])
def api_current_season():
    """Get current agricultural season info"""
    if not crop_calendar_service:
        return jsonify({'error': 'Calendar service not available'}), 503
    try:
        data = crop_calendar_service.get_current_season()
        return jsonify({'success': True, 'season': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/soil/fertilizer', methods=['GET'])
def api_soil_fertilizer():
    """Get fertilizer recommendation for a crop"""
    if not soil_service:
        return jsonify({'error': 'Soil service not available'}), 503
    crop = request.args.get('crop', '').strip()
    if not crop:
        return jsonify({'error': 'Crop parameter is required'}), 400
    try:
        data = soil_service.get_fertilizer_recommendation(crop)
        if data:
            return jsonify({'success': True, 'recommendation': data})
        return jsonify({'success': False, 'error': 'Crop not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/soil/analyze', methods=['POST'])
def api_soil_analyze():
    """Analyze soil symptoms"""
    if not soil_service:
        return jsonify({'error': 'Soil service not available'}), 503
    data = request.get_json()
    symptoms = data.get('symptoms', '').strip() if data else ''
    if not symptoms:
        return jsonify({'error': 'Symptoms parameter is required'}), 400
    try:
        analysis = soil_service.analyze_soil_symptoms(symptoms)
        return jsonify({'success': True, 'analysis': analysis})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/economics/calculate', methods=['GET'])
def api_economics_calculate():
    """Calculate farm economics for a crop"""
    if not economics_service:
        return jsonify({'error': 'Economics service not available'}), 503
    crop = request.args.get('crop', '').strip()
    area = request.args.get('area', 1.0, type=float)
    if not crop:
        return jsonify({'error': 'Crop parameter is required'}), 400
    try:
        data = economics_service.calculate_economics(crop, area)
        return jsonify({'success': True, 'economics': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/economics/compare', methods=['GET'])
def api_economics_compare():
    """Compare economics of multiple crops"""
    if not economics_service:
        return jsonify({'error': 'Economics service not available'}), 503
    crops_str = request.args.get('crops', 'wheat,rice,cotton,soybean,potato')
    crops = [c.strip() for c in crops_str.split(',') if c.strip()]
    try:
        data = economics_service.compare_crops(crops)
        return jsonify({'success': True, **data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/alerts', methods=['GET'])
def api_alerts():
    """Get agricultural alerts with language support"""
    if not alert_service:
        return jsonify({'error': 'Alert service not available'}), 503
    location = request.args.get('location', '')
    crop = request.args.get('crop', '')
    lang = request.args.get('lang', session.get('language', 'en'))
    try:
        alerts = alert_service.get_alerts(location=location, crop=crop)
        # Localize alerts — use title_<lang> and message_<lang> if available
        for alert in alerts:
            lang_title_key = f'title_{lang}'
            lang_msg_key = f'message_{lang}'
            if lang != 'en' and lang_title_key in alert:
                alert['title'] = alert[lang_title_key]
            if lang != 'en' and lang_msg_key in alert:
                alert['message'] = alert[lang_msg_key]
        return jsonify({'success': True, 'alerts': alerts})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/document-test')
def document_test():
    """Test page for document extraction feature"""
    if 'user_email' not in session:
        return redirect(url_for('login'))
    return render_template('document_test.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    lang = get_user_language()
    lang_code = convert_language_code(lang)
    
    if request.method == 'POST':
        full_name = request.form.get('fullName', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirmPassword', '')
        language_preference = request.form.get('languagePreference', 'en')
        # Normalize to 2-letter code
        language_preference = convert_language_code(language_preference)
        newsletter = request.form.get('newsletter') == 'on'
        location = request.form.get('location', '').strip()
        
        # DEBUG: Log signup attempt
        print(f"\n SIGNUP ATTEMPT:")
        print(f"   Full Name: {full_name}")
        print(f"   Email: {email}")
        print(f"   Password length: {len(password)}")
        print(f"   Language: {language_preference}")
        print(f"   Location: {location}")

        if password != confirm_password:
            flash("Passwords do not match!", "error")
            return redirect(url_for('signup'))

        # Check if email already exists
        check_query = "SELECT UserID FROM Users WHERE Email = ?"
        existing = execute_query(check_query, [email], fetch=True)
        
        if existing:
            print(f"    Email already registered!")
            flash("Email already registered!", "error")
            return redirect(url_for('signup'))

        # Hash password
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        print(f"   Password hash: {hashed_password[:30]}...")

        # Insert new user
        insert_query = """
            INSERT INTO Users (FullName, Email, PasswordHash, PreferredLanguage, Location, Role, CreatedAt)
            VALUES (?, ?, ?, ?, ?, 'Farmer', NOW())
        """
        params = (full_name, email, hashed_password, language_preference, location)
        
        result = execute_query(insert_query, params)
        
        if result:
            print(f"    User created successfully!")
            session['language'] = language_preference
            flash("Account created successfully! Please login.", "success")
            return redirect(url_for('login'))
        else:
            print(f"    Error creating account")
            flash("Error creating account. Please try again.", "error")
            return redirect(url_for('signup'))

    return render_template('signup.html', 
                         lang=lang_code, 
                         translations=translations.get(lang_code, {}))

@app.route('/login', methods=['GET', 'POST'])
def login():
    lang = get_user_language()
    lang_code = convert_language_code(lang)
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        
        # DEBUG: Log login attempt
        print(f"\n LOGIN ATTEMPT:")
        print(f"   Email: {email}")
        print(f"   Password length: {len(password)}")

        # Fetch user from database
        query = "SELECT UserID, FullName, Email, PasswordHash, PreferredLanguage FROM Users WHERE Email = ?"
        results = execute_query(query, [email], fetch=True)
        
        if not results:
            print(f"    User NOT found with email: {email}")
            flash("Invalid email or password!", "error")
            return redirect(url_for('login'))
        
        # Extract user data
        user_row = results[0]
        user_id = user_row[0]
        full_name = user_row[1]
        user_email = user_row[2]
        stored_hash = user_row[3]
        preferred_language = user_row[4] or 'english'
        
        print(f"    User found: {full_name}")
        print(f"   Hash preview: {stored_hash[:30]}...")
        
        # Verify password
        if bcrypt.check_password_hash(stored_hash, password):
            print(f"    Password verification SUCCESSFUL")
            
            session['user_email'] = user_email
            session['language'] = preferred_language
            
            # Update last login timestamp
            try:
                update_login = "UPDATE Users SET LastLogin = NOW() WHERE Email = ?"
                execute_query(update_login, [email])
            except Exception as e:
                print(f"Could not update LastLogin: {e}")
                # Non-critical error, continue with login
            
            flash("Login successful!", "success")
            return redirect(url_for('home'))
        else:
            print(f"    Password verification FAILED")
            flash("Invalid email or password!", "error")
            return redirect(url_for('login'))

    return render_template('login.html', 
                         lang=lang_code, 
                         translations=translations.get(lang_code, {}))

@app.route('/logout')
def logout():
    session.pop('user_email', None)
    session.pop('language', None)
    flash("You have been logged out.", "info")
    return redirect(url_for('home'))

# ========== FORGOT PASSWORD — OTP-BASED SECURE FLOW ==========

@app.route('/forgot-password')
def forgot_password_page():
    """Render the OTP-based forgot password page."""
    lang_code = get_user_language()
    return render_template('forgot_password.html',
                           lang=lang_code,
                           translations=translations.get(lang_code, {}))


def _send_otp_email(recipient_email, otp_code, user_name='Farmer'):
    """Send OTP email via Gmail SMTP. Returns True on success."""
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print("  SMTP not configured! Set SMTP_EMAIL and SMTP_PASSWORD in .env")
        return False
    
    msg = MIMEMultipart('alternative')
    msg['From'] = f'Krishi Sujhav <{SMTP_EMAIL}>'
    msg['To'] = recipient_email
    msg['Subject'] = f'🔐 Password Reset OTP — Krishi Sujhav'
    
    # Plain text fallback
    text_body = f"""Hello {user_name},

Your OTP for password reset is: {otp_code}

This code is valid for 5 minutes. Do NOT share it with anyone.

If you did not request this, please ignore this email.

— Team Krishi Sujhav"""
    
    # Beautiful HTML email
    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:auto;padding:24px;border:1px solid #e2e8f0;border-radius:12px;">
      <div style="text-align:center;margin-bottom:20px;">
        <div style="background:#0ea5a4;color:white;width:56px;height:56px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:24px;">🌾</div>
        <h2 style="color:#1e293b;margin:12px 0 4px;">Krishi Sujhav</h2>
        <p style="color:#64748b;font-size:14px;">Password Reset Request</p>
      </div>
      <p style="color:#334155;">Hello <strong>{user_name}</strong>,</p>
      <p style="color:#334155;">Use this OTP to reset your password:</p>
      <div style="text-align:center;margin:24px 0;">
        <div style="display:inline-block;background:#f0fdf4;border:2px dashed #0ea5a4;border-radius:10px;padding:16px 40px;">
          <span style="font-size:32px;font-weight:bold;letter-spacing:8px;color:#0ea5a4;">{otp_code}</span>
        </div>
      </div>
      <p style="color:#64748b;font-size:13px;">⏰ This code expires in <strong>5 minutes</strong>.</p>
      <p style="color:#64748b;font-size:13px;">🔒 Never share this code with anyone.</p>
      <hr style="border:none;border-top:1px solid #e2e8f0;margin:20px 0;">
      <p style="color:#94a3b8;font-size:12px;text-align:center;">If you didn't request this, you can safely ignore this email.<br>&copy; 2025 Krishi Sujhav — Farmer Assistant</p>
    </div>
    """
    
    msg.attach(MIMEText(text_body, 'plain'))
    msg.attach(MIMEText(html_body, 'html'))
    
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, recipient_email, msg.as_string())
        print(f"  ✅ OTP email sent to {recipient_email}")
        return True
    except smtplib.SMTPAuthenticationError:
        print("  ❌ SMTP Auth Error — check SMTP_EMAIL / SMTP_PASSWORD (use Gmail App Password)")
        return False
    except Exception as e:
        print(f"  ❌ SMTP Error: {e}")
        return False


@app.route('/api/send-otp', methods=['POST'])
def send_otp():
    """Generate a 6-digit OTP and email it to the user."""
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        
        if not email:
            return jsonify({'success': False, 'error': 'Email is required'}), 400
        
        # Check if user exists
        user_result = execute_query(
            "SELECT UserID, FullName FROM Users WHERE Email = ?", [email], fetch=True
        )
        if not user_result:
            return jsonify({'success': False, 'error': 'No account found with this email'}), 404
        
        user_name = user_result[0][1] or 'Farmer'
        
        # Rate limit: prevent spamming (1 OTP per 60s per email)
        if email in otp_store:
            last_sent = otp_store[email].get('sent_at')
            if last_sent and (datetime.now() - last_sent).total_seconds() < 60:
                return jsonify({'success': False, 'error': 'Please wait 60 seconds before requesting a new OTP'}), 429
        
        # Generate 6-digit OTP & unique token
        otp_code = str(random.randint(100000, 999999))
        token = secrets.token_urlsafe(32)
        
        print(f"\n🔐 OTP GENERATED for {email}: {otp_code}")
        
        # Store OTP with 5-minute expiry
        from datetime import timedelta
        otp_store[email] = {
            'otp': otp_code,
            'expiry': datetime.now() + timedelta(minutes=5),
            'verified': False,
            'attempts': 0,
            'token': token,
            'sent_at': datetime.now()
        }
        
        # Send email via SMTP
        email_sent = _send_otp_email(email, otp_code, user_name)
        
        if email_sent:
            return jsonify({
                'success': True,
                'message': 'OTP sent to your email',
                'token': token
            }), 200
        else:
            # Even if SMTP fails, don't leak OTP — just tell user to retry
            return jsonify({
                'success': False,
                'error': 'Failed to send email. Please check SMTP configuration or try again later.'
            }), 500
            
    except Exception as e:
        print(f"  Error in send_otp: {e}")
        return jsonify({'success': False, 'error': 'Server error. Please try again.'}), 500


@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    """Verify the 6-digit OTP entered by the user."""
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        otp_input = data.get('otp', '').strip()
        
        if not email or not otp_input:
            return jsonify({'success': False, 'error': 'Email and OTP are required'}), 400
        
        # Check if OTP exists for this email
        if email not in otp_store:
            return jsonify({'success': False, 'error': 'No OTP was sent to this email. Please request a new one.'}), 400
        
        stored = otp_store[email]
        
        # Check max attempts (5 tries)
        if stored['attempts'] >= 5:
            del otp_store[email]
            return jsonify({'success': False, 'error': 'Too many failed attempts. Please request a new OTP.'}), 429
        
        # Check expiry
        if datetime.now() > stored['expiry']:
            del otp_store[email]
            return jsonify({'success': False, 'error': 'OTP has expired. Please request a new one.'}), 410
        
        # Verify OTP
        stored['attempts'] += 1
        if otp_input != stored['otp']:
            remaining = 5 - stored['attempts']
            return jsonify({
                'success': False,
                'error': f'Invalid OTP. {remaining} attempt(s) remaining.'
            }), 400
        
        # OTP verified — generate a reset token
        reset_token = secrets.token_urlsafe(32)
        stored['verified'] = True
        stored['reset_token'] = reset_token
        
        print(f"  ✅ OTP verified for {email}")
        return jsonify({
            'success': True,
            'message': 'OTP verified successfully',
            'reset_token': reset_token
        }), 200
        
    except Exception as e:
        print(f"  Error in verify_otp: {e}")
        return jsonify({'success': False, 'error': 'Server error'}), 500


@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    """Reset user password after OTP verification."""
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        new_password = data.get('new_password', '')
        token = data.get('token', '')
        
        if not email or not new_password or not token:
            return jsonify({'success': False, 'error': 'All fields are required'}), 400
        
        if len(new_password) < 6:
            return jsonify({'success': False, 'error': 'Password must be at least 6 characters'}), 400
        
        # Verify the reset token
        if email not in otp_store:
            return jsonify({'success': False, 'error': 'Session expired. Please start over.'}), 400
        
        stored = otp_store[email]
        if not stored.get('verified') or stored.get('reset_token') != token:
            return jsonify({'success': False, 'error': 'Invalid or expired reset session. Please start over.'}), 403
        
        # Hash new password with bcrypt
        hashed_password = bcrypt.generate_password_hash(new_password).decode('utf-8')
        
        # Update password in database
        update_query = "UPDATE Users SET PasswordHash = ? WHERE Email = ?"
        result = execute_query(update_query, [hashed_password, email])
        
        # Clean up OTP store
        del otp_store[email]
        
        if result:
            print(f"  ✅ Password reset successful for {email}")
            return jsonify({
                'success': True,
                'message': 'Password reset successfully! Redirecting to login...'
            }), 200
        else:
            return jsonify({'success': False, 'error': 'Failed to update password'}), 500
            
    except Exception as e:
        print(f"  Error in reset_password: {e}")
        return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/change-language/<language>')
def change_language(language):
    lang_code = convert_language_code(language)
    # Always store normalized 2-letter code
    session['language'] = lang_code
    
    if 'user_email' in session:
        try:
            update_query = "UPDATE Users SET PreferredLanguage = ? WHERE Email = ?"
            execute_query(update_query, [lang_code, session['user_email']])
            print(f" Updated user language preference to: {lang_code}")
        except Exception as e:
            print(f" Error updating user language: {e}")
    
    # Check if it's an AJAX request
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json:
        return jsonify({'success': True, 'language': lang_code, 'lang_code': lang_code})
    
    return redirect(request.referrer or url_for('home'))

@app.route('/translations/translations.json')
def serve_translations():
    """Serve translations JSON file for client-side language switching"""
    try:
        translations_path = os.path.join(os.path.dirname(__file__), '../frontend/translations/translations.json')
        return send_file(translations_path, mimetype='application/json')
    except Exception as e:
        print(f"Error serving translations: {e}")
        return jsonify({'error': 'Translations not found'}), 404

@app.route('/api/translate', methods=['POST'])
def translate_text():
    """Translate text to target language using Gemini AI"""
    if 'user_email' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    
    try:
        data = request.json
        text = data.get('text', '')
        target_language = data.get('target_language', 'en')
        
        if not text:
            return jsonify({'error': 'No text provided'}), 400
        
        # Language mapping
        lang_map = {
            'en': 'English',
            'hi': 'Hindi',
            'mr': 'Marathi',
            'pa': 'Punjabi',
            'ml': 'Malayalam',
            'ta': 'Tamil',
            'te': 'Telugu',
            'kn': 'Kannada'
        }
        
        target_lang_name = lang_map.get(target_language, 'English')
        
        # Use Gemini to translate
        if farming_ai and hasattr(farming_ai, 'model') and farming_ai.model:
            try:
                translation_prompt = f"Translate the following farming-related text to {target_lang_name}. Only provide the translation, no explanations:\n\n{text}"
                
                response = farming_ai.model.generate_content(translation_prompt)
                translated_text = response.text.strip()
                
                return jsonify({
                    'success': True,
                    'translated_text': translated_text,
                    'target_language': target_language
                })
            except Exception as e:
                print(f"Gemini translation error: {e}")
                # Fallback: return original text
                return jsonify({
                    'success': True,
                    'translated_text': text,
                    'target_language': target_language,
                    'note': 'Translation unavailable, returning original text'
                })
        else:
            # No AI available, return original
            return jsonify({
                'success': True,
                'translated_text': text,
                'target_language': target_language,
                'note': 'Translation service unavailable'
            })
            
    except Exception as e:
        print(f" Translation API error: {e}")
        return jsonify({'error': 'Translation failed', 'details': str(e)}), 500

@app.route('/upload-file', methods=['POST'])
def upload_file():
    if 'user_email' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file selected'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        user_email = session['user_email']
        user_folder = os.path.join(app.config['UPLOAD_FOLDER'], user_email)
        
        os.makedirs(user_folder, exist_ok=True)
        
        file_path = os.path.join(user_folder, filename)
        file.save(file_path)
        
        # Optional: Save file metadata to database if needed
        # For now, we just save to disk
        
        return jsonify({
            'success': True,
            'message': 'File uploaded successfully',
            'filename': filename
        })
    
    return jsonify({'error': 'Invalid file type'}), 400

@app.route('/upload-audio', methods=['POST'])
def upload_audio():
    if 'user_email' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file'}), 400
    
    audio_file = request.files['audio']
    user_email = session['user_email']
    user_folder = os.path.join(app.config['UPLOAD_FOLDER'], user_email)
    
    os.makedirs(user_folder, exist_ok=True)
    
    filename = f"voice_message_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
    file_path = os.path.join(user_folder, filename)
    
    audio_file.save(file_path)
    
    # Optional: Save audio metadata to database if needed
    # For now, we just save to disk
    
    return jsonify({
        'success': True,
        'message': 'Voice message uploaded successfully',
        'filename': filename
    })


# ----------------------
# ML Model Prediction Endpoint
# ----------------------
try:
    from .ml_model import load_default_model, MLModel
except Exception:
    # try relative import fallback
    try:
        from ml_model import load_default_model, MLModel
    except Exception:
        load_default_model = None
        MLModel = None

# Global cached ML model instance (loaded on first use)
ml_model_instance = None

# Helper to get or load the ML model once
def get_ml_model():
    global ml_model_instance
    if ml_model_instance is not None:
        return ml_model_instance

    model_dir = os.path.join(os.path.dirname(__file__), 'models')
    model_path = os.path.join(model_dir, os.getenv('ML_MODEL_FILE', 'best_model_finetuned.h5'))
    classes_path = os.path.join(model_dir, os.getenv('ML_CLASSES_FILE', 'classes.pkl'))

    if MLModel is None:
        raise RuntimeError('MLModel support not available (missing dependencies)')

    ml_model_instance = MLModel(model_path, classes_path)
    return ml_model_instance


@app.route('/api/predict', methods=['POST'])
def predict():
    """Accept an image file and return model predictions.

    Expects multipart form with field 'file'.
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    filename = secure_filename(file.filename)
    tmp_folder = os.path.join(app.config['UPLOAD_FOLDER'], 'tmp')
    os.makedirs(tmp_folder, exist_ok=True)
    file_path = os.path.join(tmp_folder, filename)
    file.save(file_path)

    # Load model lazily
    try:
        if MLModel is None:
            return jsonify({'error': 'ML support not installed on server'}), 500

        ml = get_ml_model()
        preds = ml.predict_image(file_path, top_k=3)
        
        # Check if top predictions are consistent (same plant type)
        top_plant = ml.get_plant_type(preds[0][0]) if preds else None
        consistency_warning = None
        
        if len(preds) >= 2:
            plant_types = [ml.get_plant_type(p[0]) for p in preds[:3]]
            unique_types = set(plant_types)
            
            # If multiple plant types in top predictions, add warning
            if len(unique_types) > 1 and preds[0][1] < 0.8:
                consistency_warning = f"Model confidence is low ({preds[0][1]*100:.0f}%). Consider uploading a clearer image or different angle."

        # Format response
        response_data = {
            'success': True,
            'predictions': [
                {'label': p[0], 'confidence': p[1]} for p in preds
            ]
        }
        
        if consistency_warning:
            response_data['warning'] = consistency_warning
            
        return jsonify(response_data)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 500
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 500
    except Exception as e:
        print(f"Error during prediction: {e}")
        return jsonify({'error': 'Prediction failed'}), 500


# ----------------------
# Document Extraction API Routes
# ----------------------

@app.route('/api/document/upload', methods=['POST'])
def upload_document():
    """Upload, extract text, and get AI analysis in one call"""
    if 'user_email' not in session:
        return jsonify({'error': 'Authentication required', 'redirect': '/login'}), 401
    
    if document_extractor is None:
        return jsonify({'error': 'Document extractor not available. Please install required packages.'}), 500
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not supported'}), 400
    
    try:
        user_email = session['user_email']
        
        # Create user-specific folder
        user_folder = os.path.join(app.config['UPLOAD_FOLDER'], user_email, 'documents')
        os.makedirs(user_folder, exist_ok=True)
        
        # Save file with secure filename
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_filename = f"{timestamp}_{filename}"
        file_path = os.path.join(user_folder, unique_filename)
        
        file.save(file_path)
        
        # Extract text from document (fast extraction)
        print(f"Extracting text from: {unique_filename}")
        extracted_text = document_extractor.extract_text(file_path)
        
        # Check if extraction was successful
        if extracted_text.startswith("Error"):
            return jsonify({
                'error': 'Failed to extract text',
                'details': extracted_text
            }), 500
        
        print(f"Extracted {len(extracted_text)} characters")
        
        # Get language from request
        language = request.form.get('language', 'en')
        
        # Language mapping
        language_names = {
            'en': 'English',
            'hi': 'Hindi',
            'mr': 'Marathi',
            'pa': 'Punjabi',
            'ml': 'Malayalam',
            'ta': 'Tamil',
            'te': 'Telugu',
            'kn': 'Kannada'
        }
        lang_name = language_names.get(language, 'English')
        
        # Optimize prompt with text preview (first 2000 chars for faster AI processing)
        text_preview = extracted_text[:2000] if len(extracted_text) > 2000 else extracted_text
        
        prompt = f"""You are an expert agricultural advisor analyzing a farming document.

Document Content:
{text_preview}

Analyze this farming document and provide:
1. Key insights and main topics
2. Important recommendations for farmers
3. Practical action items or advice

Respond in {lang_name} in a clear, helpful manner."""

        print(f"Getting AI analysis...")
        # Call Gemini API
        ai_response = farming_ai._get_gemini_response(prompt, language)
        print(f"AI analysis complete")
        
        # Return everything in one response
        return jsonify({
            'success': True,
            'filename': unique_filename,
            'original_filename': filename,
            'extracted_text': extracted_text,
            'text_length': len(extracted_text),
            'file_path': file_path,
            'ai_analysis': ai_response
        })
        
    except Exception as e:
        print(f"Error uploading document: {e}")
        return jsonify({'error': f'Failed to process document: {str(e)}'}), 500


@app.route('/api/document/extract', methods=['POST'])
def extract_from_existing():
    """Extract text from an already uploaded document"""
    if 'user_email' not in session:
        return jsonify({'error': 'Authentication required', 'redirect': '/login'}), 401
    
    if document_extractor is None:
        return jsonify({'error': 'Document extractor not available'}), 500
    
    data = request.get_json()
    file_path = data.get('file_path', '')
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    
    try:
        # Extract text
        extracted_text = document_extractor.extract_text(file_path)
        
        if extracted_text.startswith("Error"):
            return jsonify({
                'error': 'Failed to extract text',
                'details': extracted_text
            }), 500
        
        return jsonify({
            'success': True,
            'extracted_text': extracted_text,
            'text_length': len(extracted_text)
        })
        
    except Exception as e:
        return jsonify({'error': f'Extraction failed: {str(e)}'}), 500


@app.route('/api/document/chat', methods=['POST'])
def chat_with_document():
    """Chat with AI about uploaded document content"""
    if 'user_email' not in session:
        return jsonify({'error': 'Authentication required', 'redirect': '/login'}), 401
    
    data = request.get_json()
    document_text = data.get('document_text', '')
    user_question = data.get('question', '')
    language = data.get('language', 'en')
    document_count = data.get('document_count', 1)
    
    if not document_text or not user_question:
        return jsonify({'error': 'Document text and question are required'}), 400
    
    try:
        # Language names mapping
        language_names = {
            'en': 'English',
            'hi': 'Hindi',
            'mr': 'Marathi',
            'pa': 'Punjabi',
            'ml': 'Malayalam',
            'ta': 'Tamil',
            'te': 'Telugu',
            'kn': 'Kannada'
        }
        lang_name = language_names.get(language, 'English')
        
        # Check if question is farming-related
        if not farming_ai.is_farming_related(user_question):
            return jsonify({
                'success': True,
                'response': farming_ai.get_out_of_domain_response(language)
            })
        
        # Build optimized prompt - limit document text to 2000 chars for faster processing
        doc_preview = document_text[:2000] + "..." if len(document_text) > 2000 else document_text
        
        if document_count > 1:
            prompt = f"""You are an expert agricultural advisor analyzing {document_count} farming documents.

Document Content (preview):
{doc_preview}

Farmer's Question: {user_question}

Provide a comprehensive answer in {lang_name} that:
1. Directly answers the question
2. References relevant document information
3. Gives practical, actionable farming advice

Respond in {lang_name} ONLY."""
        else:
            prompt = f"""You are an expert agricultural advisor analyzing a farming document.

Document Content:
{doc_preview}

Farmer's Question: {user_question}

Provide a detailed answer in {lang_name} based on the document that:
1. Directly answers their question
2. Provides practical, actionable advice
3. References specific parts of the document

Respond in {lang_name} ONLY."""
        
        # Get AI response
        ai_response = farming_ai._get_gemini_response(prompt, language)
        
        return jsonify({
            'success': True,
            'response': ai_response
        })
        
    except Exception as e:
        print(f"Error in document chat: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Failed to analyze document: {str(e)}'}), 500
        return jsonify({
            'error': 'Failed to generate response',
            'details': str(e)
        }), 500


@app.route('/api/document/delete', methods=['POST'])
def delete_document():
    """Delete a specific uploaded document"""
    if 'user_email' not in session:
        return jsonify({'error': 'Authentication required', 'redirect': '/login'}), 401
    
    data = request.get_json()
    file_path = data.get('file_path', '')
    
    if not file_path:
        return jsonify({'error': 'File path is required'}), 400
    
    try:
        user_email = session['user_email']
        
        # Security check: ensure file belongs to current user
        user_folder = os.path.join(app.config['UPLOAD_FOLDER'], user_email, 'documents')
        
        # Normalize paths for comparison
        file_path_normalized = os.path.normpath(file_path)
        user_folder_normalized = os.path.normpath(user_folder)
        
        # Check if file is in user's folder
        if not file_path_normalized.startswith(user_folder_normalized):
            return jsonify({'error': 'Unauthorized access'}), 403
        
        # Check if file exists
        if not os.path.exists(file_path):
            return jsonify({'error': 'File not found'}), 404
        
        # Delete the file
        os.remove(file_path)
        print(f"Deleted document: {file_path}")
        
        return jsonify({
            'success': True,
            'message': 'Document deleted successfully'
        })
        
    except Exception as e:
        print(f"Error deleting document: {e}")
        return jsonify({
            'error': 'Failed to delete document',
            'details': str(e)
        }), 500


@app.route('/api/document/batch-extract', methods=['POST'])
def batch_extract():
    """Extract text from multiple documents in a directory"""
    if 'user_email' not in session:
        return jsonify({'error': 'Authentication required', 'redirect': '/login'}), 401
    
    if document_extractor is None:
        return jsonify({'error': 'Document extractor not available'}), 500
    
    try:
        user_email = session['user_email']
        user_doc_folder = os.path.join(app.config['UPLOAD_FOLDER'], user_email, 'documents')
        
        if not os.path.exists(user_doc_folder):
            return jsonify({'error': 'No documents folder found'}), 404
        
        # Extract from all documents in folder
        results = document_extractor.extract_from_directory(user_doc_folder)
        
        return jsonify({
            'success': True,
            'results': results,
            'total_files': len(results)
        })
        
    except Exception as e:
        return jsonify({'error': f'Batch extraction failed: {str(e)}'}), 500


# ===== VOICE INTERACTION SYSTEM =====

@app.route('/api/voice/transcribe', methods=['POST'])
def transcribe_audio():
    """Handle audio transcription endpoint"""
    try:
        data = request.get_json()
        transcript = data.get('transcript', '')
        language = data.get('language', 'en')
        confidence = data.get('confidence', 0)
        
        return jsonify({
            'success': True,
            'transcript': transcript,
            'language': language,
            'confidence': confidence,
            'ready_for_processing': True
        })
        
    except Exception as e:
        print(f"Transcription error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/voice/chat-stream', methods=['POST'])
def voice_chat_stream():
    """Stream chat response for voice interaction"""
    try:
        data = request.get_json()
        message = data.get('message', '').strip()
        language = data.get('language', 'en')
        user_id = session.get('user_id')
        
        if not message:
            return jsonify({'error': 'No message provided'}), 400
        
        def generate_stream():
            try:
                # Get FarmingAI instance
                farming_ai = app.farming_ai
                
                # Generate response
                response = farming_ai.get_farming_response(message, language)
                
                # Save to chat history
                if user_id:
                    farming_ai._save_chat_message(user_id, message, 'user')
                    farming_ai._save_chat_message(user_id, response, 'assistant')
                
                # Stream response word by word
                words = response.split()
                accumulated_text = ''
                
                for i, word in enumerate(words):
                    accumulated_text += word + ' '
                    chunk_data = {
                        'word': word,
                        'accumulated': accumulated_text.strip(),
                        'is_complete': i == len(words) - 1,
                        'index': i,
                        'total_words': len(words)
                    }
                    yield f"data: {json.dumps(chunk_data)}\n\n"
                    time.sleep(0.03)  # Streaming delay for effect
                
                # Send completion signal
                yield f"data: {json.dumps({'complete': True, 'full_response': response})}\n\n"
                
            except Exception as e:
                error_data = {'error': str(e), 'complete': True}
                yield f"data: {json.dumps(error_data)}\n\n"
        
        return Response(
            generate_stream(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Cache-Control'
            }
        )
        
    except Exception as e:
        print(f"Voice chat stream error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/voice/tts-config', methods=['GET'])
def get_tts_config():
    """Get text-to-speech configuration for different languages"""
    tts_config = {
        'en': {
            'lang': 'en-US',
            'voice': 'Google US English',
            'rate': 1.0,
            'pitch': 1.0,
            'volume': 1.0
        },
        'hi': {
            'lang': 'hi-IN',
            'voice': 'Google हिन्दी',
            'rate': 0.9,
            'pitch': 1.0,
            'volume': 1.0
        },
        'mr': {
            'lang': 'mr-IN',
            'voice': 'Google मराठी',
            'rate': 0.9,
            'pitch': 1.0,
            'volume': 1.0
        },
        'pa': {
            'lang': 'pa-IN',
            'voice': 'Google ਪੰਜਾਬੀ',
            'rate': 0.9,
            'pitch': 1.0,
            'volume': 1.0
        },
        'ml': {
            'lang': 'ml-IN',
            'voice': 'Google മലയാളം',
            'rate': 0.9,
            'pitch': 1.0,
            'volume': 1.0
        }
    }
    
    return jsonify(tts_config)

@app.route('/api/voice/session', methods=['POST'])
def manage_voice_session():
    """Manage voice interaction session state"""
    try:
        data = request.get_json()
        action = data.get('action')  # 'start', 'stop', 'pause', 'resume'
        
        if 'voice_session' not in session:
            session['voice_session'] = {
                'active': False,
                'language': 'en',
                'start_time': None,
                'interaction_count': 0,
                'paused': False
            }
        
        voice_session = session['voice_session']
        
        if action == 'start':
            voice_session['active'] = True
            voice_session['language'] = data.get('language', 'en')
            voice_session['start_time'] = datetime.now().isoformat()
            voice_session['interaction_count'] = 0
            voice_session['paused'] = False
            
        elif action == 'stop':
            voice_session['active'] = False
            voice_session['paused'] = False
            
        elif action == 'pause':
            voice_session['paused'] = True
            
        elif action == 'resume':
            voice_session['paused'] = False
            
        elif action == 'increment':
            voice_session['interaction_count'] = voice_session.get('interaction_count', 0) + 1
            
        session['voice_session'] = voice_session
        session.modified = True
        
        return jsonify({
            'success': True,
            'session': voice_session
        })
        
    except Exception as e:
        print(f"Voice session error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/voice/analyze-image', methods=['POST'])
def voice_analyze_image():
    """Analyze image with voice response"""
    try:
        # Get image and language
        language = request.form.get('language', 'en')
        
        # Handle base64 image data
        if 'image_data' in request.form:
            image_data = request.form.get('image_data')
            # Remove data:image/jpeg;base64, prefix
            if ',' in image_data:
                image_data = image_data.split(',')[1]
            
            # Decode base64 image
            image_bytes = base64.b64decode(image_data)
            
            # Save temporary file
            temp_path = os.path.join(app.config['UPLOAD_FOLDER'], 'temp', f'voice_image_{uuid.uuid4().hex}.jpg')
            os.makedirs(os.path.dirname(temp_path), exist_ok=True)
            
            with open(temp_path, 'wb') as f:
                f.write(image_bytes)
                
        # Handle regular file upload
        elif 'image' in request.files:
            image_file = request.files['image']
            temp_path = os.path.join(app.config['UPLOAD_FOLDER'], 'temp', f'voice_image_{uuid.uuid4().hex}.jpg')
            os.makedirs(os.path.dirname(temp_path), exist_ok=True)
            image_file.save(temp_path)
        else:
            return jsonify({'error': 'No image provided'}), 400
        
        # Get ML prediction
        predictions = app.farming_ai.get_ml_model().predict_image(temp_path, top_k=3)
        
        if predictions:
            # Format results for voice
            disease_name = predictions[0][0]
            confidence = predictions[0][1] * 100
            plant_type = app.farming_ai.get_ml_model().get_plant_type(disease_name)
            
            # Create voice-friendly response based on language
            if language == 'hi':
                if confidence < 70:
                    voice_text = f"मैंने {confidence:.1f}% विश्वास के साथ {disease_name} की पहचान की है। हालांकि, विश्वास कम है। बेहतर सटीकता के लिए कृपया एक स्पष्ट तस्वीर लें।"
                else:
                    voice_text = f"मैंने {confidence:.1f}% विश्वास के साथ {disease_name} की पहचान की है। यह एक विश्वसनीय निदान प्रतीत होता है।"
            elif language == 'mr':
                if confidence < 70:
                    voice_text = f"मी {confidence:.1f}% विश्वासाने {disease_name} ओळखले आहे. तथापि, आत्मविश्वास कमी आहे. चांगल्या अचूकतेसाठी कृपया स्पष्ट फोटो घ्या."
                else:
                    voice_text = f"मी {confidence:.1f}% विश्वासाने {disease_name} ओळखले आहे. हे विश्वसनीय निदान दिसते."
            else:  # English and other languages
                if confidence < 70:
                    voice_text = f"I detected {disease_name} with {confidence:.1f}% confidence. However, the confidence is low. Please take a clearer photo for better accuracy."
                else:
                    voice_text = f"I detected {disease_name} with {confidence:.1f}% confidence. This appears to be a reliable diagnosis."
            
            # Get farming advice
            advice_query = f"Give treatment advice for {disease_name} in {plant_type} plants"
            advice = app.farming_ai.get_farming_response(advice_query, language)
            
            response_data = {
                'success': True,
                'disease': disease_name,
                'confidence': confidence,
                'plant_type': plant_type,
                'voice_text': voice_text,
                'advice': advice,
                'all_predictions': [(pred[0], pred[1] * 100) for pred in predictions],
                'language': language,
                'formatted_name': app.farming_ai._format_disease_name(disease_name),
                'plant_emoji': app.farming_ai._get_plant_emoji(disease_name)
            }
        else:
            if language == 'hi':
                error_voice = 'मैं इस छवि का विश्लेषण नहीं कर सका। कृपया पत्ती का स्पष्ट फोटो लेने का प्रयास करें।'
            elif language == 'mr':
                error_voice = 'मी या प्रतिमेचे विश्लेषण करू शकलो नाही. कृपया पानाचा स्पष्ट फोटो घेण्याचा प्रयत्न करा.'
            else:
                error_voice = 'I could not analyze this image. Please try taking a clearer photo of the plant leaf.'
                
            response_data = {
                'success': False,
                'error': 'Could not analyze image',
                'voice_text': error_voice,
                'language': language
            }
        
        # Clean up temporary file
        try:
            os.remove(temp_path)
        except:
            pass
        
        return jsonify(response_data)
        
    except Exception as e:
        print(f"Voice image analysis error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/voice/quick-commands', methods=['GET'])
def get_voice_commands():
    """Get available voice commands"""
    commands = {
        'en': {
            'weather': ['weather', 'temperature', 'rain', 'forecast'],
            'disease': ['disease', 'identify', 'diagnose', 'analyze'],
            'crop': ['crop recommendation', 'what to plant', 'planting advice'],
            'help': ['help', 'commands', 'what can you do']
        },
        'hi': {
            'weather': ['मौसम', 'तापमान', 'बारिश', 'पूर्वानुमान'],
            'disease': ['बीमारी', 'पहचान', 'निदान', 'विश्लेषण'],
            'crop': ['फसल सिफारिश', 'क्या लगाएं', 'रोपण सलाह'],
            'help': ['मदद', 'कमांड', 'आप क्या कर सकते हैं']
        },
        'mr': {
            'weather': ['हवामान', 'तापमान', 'पाऊस', 'अंदाज'],
            'disease': ['रोग', 'ओळख', 'निदान', 'विश्लेषण'],
            'crop': ['पीक शिफारस', 'काय लावायचे', 'लागवड सल्ला'],
            'help': ['मदत', 'आज्ञा', 'तुम्ही काय करू शकता']
        }
    }
    
    return jsonify(commands)


if __name__ == '__main__':
    # Railway sets PORT env var; fallback to 5000 for local dev
    port = int(os.getenv('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port, use_reloader=False)
