from flask import Flask, request, jsonify, send_file
import subprocess
import os
import json
import requests
import tempfile
import spacy
from serpapi import GoogleSearch
from urllib.parse import quote, urlparse, parse_qs
import re
import logging
from pathlib import Path

# --- Config - Use Environment Variables for Security ---
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "9184855c3a7f4401806ebdc8ba1c35bf169b449c808d6bf9baca859376d1b4e5")
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "AIzaSyC9v39XIK9P-uzJCmvAN1OK7AUGyvxZUH0")
OPENCAGE_API_KEY = os.environ.get("OPENCAGE_API_KEY", "048fafc1e4cf46e7adc98464f21bcae5")

app = Flask(__name__)

# Handle SpaCy model for deployment
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    import sys
    subprocess.run([sys.executable, "-m", "spacy", "download", "en_core_web_sm"], check=True)
    nlp = spacy.load("en_core_web_sm")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Core Functions ---
def convert_serpapi_to_google_maps(url):
    """Convert SerpApi URL to Google Maps URL."""
    try:
        parsed_url = urlparse(url)
        if 'serpapi.com' not in parsed_url.netloc:
            logger.info(f"URL {url} is not a SerpApi URL")
            return None, None
        query_params = parse_qs(parsed_url.query)
        place_id = query_params.get('place_id', [None])[0]
        if place_id:
            maps_url = f"https://www.google.com/maps/place/?q=place_id:{place_id}"
            logger.info(f"---------------------\n*** INITIAL GOOGLE MAPS LINK ***\n{maps_url}\n---------------------")
            return maps_url, place_id
        logger.error(f"No place_id found in SerpApi URL: {url}")
        return None, None
    except Exception as e:
        logger.error(f"Error converting SerpApi URL {url}: {e}")
        return None, None

def finalize_maps_url(url):
    """Check and convert SerpApi URL to Google Maps URL if needed."""
    if not url:
        return None
    parsed_url = urlparse(url)
    if 'serpapi.com' in parsed_url.netloc:
        maps_url, _ = convert_serpapi_to_google_maps(url)
        if maps_url:
            logger.info(f"Converted final SerpApi URL to: {maps_url}")
            return maps_url
    return url

def get_place_details_from_id(place_id, fallback_maps_url):
    """Fetch place details using Google Maps API."""
    try:
        url = f"https://places.googleapis.com/v1/places/{place_id}"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
            "X-Goog-FieldMask": "displayName,formattedAddress,location,googleMapsUri"
        }
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        result = response.json()
        logger.info(f"Google Maps place details for place_id {place_id}: {result}")
        
        if 'error' not in result:
            lat = result.get("location", {}).get("latitude")
            lon = result.get("location", {}).get("longitude")
            name = result.get("displayName", {}).get("text", "Unknown Place")
            address = result.get("formattedAddress", "Unknown Address")
            maps_url = result.get("googleMapsUri") or fallback_maps_url
            
            if lat and lon:
                maps_url = f"https://www.google.com/maps/search/{quote(name)}/@{lat},{lon},17z"
            
            logger.info(f"---------------------\n*** FINAL GOOGLE MAPS LINK ***\n{maps_url}\n---------------------")
            return {
                "name": name,
                "address": address,
                "lat": lat,
                "lon": lon,
                "maps_url": maps_url,
                "source": "google_maps_api_place_id"
            }
        else:
            logger.error(f"Google Maps API error: {result.get('error', 'No error message')}")
            
    except requests.RequestException as e:
        logger.error(f"Google Maps API request failed for place_id {place_id}: {e}")
    except Exception as e:
        logger.error(f"Error fetching place details for place_id {place_id}: {e}")
    
    # Fallback response
    logger.info(f"---------------------\n*** FALLBACK GOOGLE MAPS LINK ***\n{fallback_maps_url}\n---------------------")
    return {
        "name": "Unknown Place",
        "address": "Unknown Address",
        "lat": None,
        "lon": None,
        "maps_url": fallback_maps_url,
        "source": "google_maps_api_error"
    }

def extract_reel_location_fallback(url):
    """Alternative Instagram extraction with multiple strategies."""
    strategies = [
        # Strategy 1: Basic metadata extraction
        ['yt-dlp', '--dump-json', '--no-download', '--ignore-errors'],
        # Strategy 2: With user agent
        ['yt-dlp', '--dump-json', '--no-download', '--ignore-errors',
         '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'],
        # Strategy 3: With cookies (if available)
        ['yt-dlp', '--dump-json', '--no-download', '--ignore-errors',
         '--cookies-from-browser', 'chrome']
    ]
    
    for i, strategy in enumerate(strategies):
        try:
            result = subprocess.run(
                strategy + [url],
                capture_output=True, text=True, timeout=30
            )
            
            if result.returncode == 0 and result.stdout:
                info = json.loads(result.stdout)
                description = info.get("description", "")
                if description:
                    logger.info(f"Strategy {i+1} succeeded: extracted description")
                    return description
                    
        except Exception as e:
            logger.error(f"Strategy {i+1} failed: {e}")
            continue
    
    # Web scraping fallback
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
        }
        
        # Try embed URL which is less restricted
        embed_url = url.replace('/reel/', '/p/').replace('?', '/embed/?')
        response = requests.get(embed_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            content = response.text
            # Look for JSON-LD data
            json_ld_match = re.search(r'<script type="application/ld\+json">([^<]+)</script>', content)
            if json_ld_match:
                try:
                    data = json.loads(json_ld_match.group(1))
                    if 'caption' in data:
                        return data['caption']
                except:
                    pass
            
            # Look for description in meta tags
            desc_match = re.search(r'<meta name="description" content="([^"]*)"', content)
            if desc_match:
                return desc_match.group(1)
                
    except Exception as e:
        logger.error(f"Web scraping fallback failed: {e}")
    
    return ""

def download_reel(url, output_path):
    """Download Instagram reel using yt-dlp with improved error handling."""
    strategies = [
        # Strategy 1: Basic download
        ["yt-dlp", "-f", "best[ext=mp4]", "-o", output_path],
        # Strategy 2: With user agent and headers
        ["yt-dlp", "-f", "best[ext=mp4]", "-o", output_path,
         "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
         "--add-header", "Accept:text/html,application/xhtml+xml"]
    ]
    
    for i, strategy in enumerate(strategies):
        try:
            subprocess.run(strategy + [url], check=True, timeout=120)
            logger.info(f"Downloaded reel to {output_path} using strategy {i+1}")
            return output_path
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.error(f"Download strategy {i+1} failed: {e}")
            if i == len(strategies) - 1:  # Last strategy
                raise
            continue
    
    return output_path

def extract_description(url):
    """Extract reel description using multiple fallback methods."""
    description = extract_reel_location_fallback(url)
    if description:
        logger.info(f"Extracted description: {description[:150]}...")
        return description
    else:
        logger.warning("Could not extract description from Instagram reel")
        return ""

def extract_location_name(text):
    """Extract location names using SpaCy NLP."""
    if not text:
        return []
    
    try:
        doc = nlp(text)
        locations = [ent.text for ent in doc.ents if ent.label_ in ["GPE", "FAC", "ORG", "LOC"]]
        logger.info(f"Extracted location names: {locations}")
        return locations
    except Exception as e:
        logger.error(f"Error in location extraction: {e}")
        return []

def extract_business_name(text):
    """Extract business name or Instagram handle."""
    if not text:
        return None
        
    # Look for Instagram handles
    match = re.search(r'@(\w+)', text)
    if match:
        business = match.group(1)
        logger.info(f"Extracted business name from handle: {business}")
        return business
    
    # Use NLP to find organization or facility names
    try:
        doc = nlp(text)
        for ent in doc.ents:
            if ent.label_ in ["ORG", "FAC"]:
                logger.info(f"Extracted business name from NLP: {ent.text}")
                return ent.text
    except Exception as e:
        logger.error(f"Error in business name extraction: {e}")
    
    return None

def clean_location_block(location_block):
    """Clean and standardize location block for better geocoding."""
    if not location_block:
        return ""
    
    # Remove social media handles and clean up formatting
    cleaned = re.sub(r'@\w+\.\w+', '', location_block).strip()
    cleaned = re.sub(r'@\w+', '', cleaned).strip()
    cleaned = re.sub(r'371302', '682025', cleaned).strip()  # Fix common postal code error
    cleaned = re.sub(r'\s*,\s*', ', ', cleaned).strip(', ')
    
    # Add location context for better results (customize based on your region)
    if "Kochi" not in cleaned and "Kerala" not in cleaned:
        cleaned += ", Kochi, Kerala, 682025"
    elif "Kochi" in cleaned and "Kerala" not in cleaned:
        cleaned += ", Kerala, 682025"
    elif "Kerala" in cleaned and "682025" not in cleaned:
        cleaned += ", 682025"
    
    # Remove extra spaces and fix formatting
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    logger.info(f"Cleaned location block: '{cleaned}'")
    return cleaned

def google_maps_search(query, business_name=None):
    """Search for location using Google Maps Places API with SerpAPI fallback."""
    search_query = f"{business_name}, {query}" if business_name else query
    logger.info(f"Searching Google Maps for: '{search_query}'")
    
    # Primary: Google Maps Places API
    try:
        url = "https://places.googleapis.com/v1/places:searchText"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.googleMapsUri,places.id"
        }
        payload = {
            "textQuery": search_query,
            "languageCode": "en",
            "regionCode": "IN"
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        results = response.json()
        logger.info(f"Google Maps API results: {results}")

        if results.get("places"):
            place = results["places"][0]
            lat = place["location"]["latitude"]
            lon = place["location"]["longitude"]
            name = place.get("displayName", {}).get("text", search_query)
            maps_url = place.get("googleMapsUri") or f"https://www.google.com/maps/search/{quote(name)}/@{lat},{lon},17z"
            
            return {
                "name": name,
                "address": place.get("formattedAddress", query),
                "lat": lat,
                "lon": lon,
                "maps_url": maps_url,
                "source": "google_maps_api"
            }
        else:
            logger.warning(f"Google Maps API returned no places: {results.get('status', 'No status')}")
            
    except Exception as e:
        logger.error(f"Google Maps API search failed: {e}")

    # Fallback: SerpAPI Google Maps
    try:
        params = {
            "engine": "google_maps",
            "q": search_query,
            "ll": "@9.931233,76.267304,15z",  # Kochi, Kerala coordinates
            "type": "search",
            "api_key": SERPAPI_KEY
        }
        
        search = GoogleSearch(params)
        results = search.get_dict()
        logger.info(f"SerpAPI results: {results}")

        # Check for place results first
        if "place_results" in results:
            place = results["place_results"]
            coords = place.get("gps_coordinates", {})
            lat = coords.get("latitude")
            lon = coords.get("longitude")
            name = place.get("title", search_query)
            maps_url = place.get("place_id_search") or f"https://www.google.com/maps/search/{quote(name)}/@{lat},{lon},17z"
            
            return {
                "name": name,
                "address": place.get("address", query),
                "lat": lat,
                "lon": lon,
                "maps_url": maps_url,
                "source": "serpapi_places"
            }
        
        # Check local results
        elif "local_results" in results and results["local_results"]:
            place = results["local_results"][0]
            coords = place.get("gps_coordinates", {})
            lat = coords.get("latitude")
            lon = coords.get("longitude")
            name = place.get("title", search_query)
            place_results_link = place.get("links", {}).get("place_results")
            maps_url = place_results_link or f"https://www.google.com/maps/search/{quote(name)}/@{lat},{lon},17z"
            
            return {
                "name": name,
                "address": place.get("address", query),
                "lat": lat,
                "lon": lon,
                "maps_url": maps_url,
                "source": "serpapi_local"
            }
        
        logger.warning("SerpAPI returned no useful results")
        return None
        
    except Exception as e:
        logger.error(f"SerpAPI Google Maps search failed: {e}")
        return None

def get_coordinates_from_maps(url):
    """Extract coordinates from Google Maps URL."""
    try:
        if "@" in url:
            coords_part = url.split("@")[1].split(",")
            lat, lon = float(coords_part[0]), float(coords_part[1])
            logger.info(f"Extracted coordinates from maps URL: {lat},{lon}")
            return lat, lon
        return None, None
    except Exception as e:
        logger.error(f"Error extracting coordinates from maps URL: {e}")
        return None, None

def get_coordinates_from_address(address):
    """Geocode address using OpenCage with Google Maps fallback."""
    if not address:
        return None, None
        
    try:
        # Clean and refine the address
        address_parts = address.split(", ")
        refined_address = ", ".join(part for part in address_parts if part not in ["India", "Ernakulam"])
        
        if "Kochi" not in refined_address and "Kerala" not in refined_address:
            refined_address += ", Kochi, Kerala, 682025"
        
        logger.info(f"Geocoding refined address: '{refined_address}'")
        
        # Primary: OpenCage Geocoding API
        response = requests.get("https://api.opencagedata.com/geocode/v1/json", 
                              params={
                                  "q": refined_address,
                                  "key": OPENCAGE_API_KEY,
                                  "limit": 1,
                                  "countrycode": "in"
                              }, timeout=10)
        
        data = response.json()
        logger.info(f"OpenCage result: {data}")
        
        if data.get("results"):
            geometry = data["results"][0]["geometry"]
            lat = geometry["lat"]
            lon = geometry["lng"]
            logger.info(f"OpenCage geocoding successful: {lat}, {lon}")
            return lat, lon
        else:
            logger.warning("OpenCage found no results, trying Google Maps geocoding")
            
    except Exception as e:
        logger.error(f"OpenCage geocoding error: {e}")

    # Fallback: Google Maps Geocoding API
    try:
        geocode_url = f"https://maps.googleapis.com/maps/api/geocode/json?address={quote(refined_address)}&key={GOOGLE_MAPS_API_KEY}"
        response = requests.get(geocode_url, timeout=10)
        data = response.json()
        logger.info(f"Google Maps Geocoding result: {data}")
        
        if data["status"] == "OK" and data["results"]:
            location = data["results"][0]["geometry"]["location"]
            lat = location["lat"]
            lon = location["lng"]
            logger.info(f"Google Maps geocoding successful: {lat}, {lon}")
            return lat, lon
        else:
            logger.warning(f"Google Maps Geocoding failed with status: {data.get('status')}")
            
    except Exception as e:
        logger.error(f"Google Maps Geocoding error: {e}")
    
    return None, None

# --- Flask Routes ---

@app.route("/")
def index():
    """Serve the main application page with embedded HTML."""
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
      <title>Instagram Reel Location Finder</title>
      <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
      <link rel="icon" type="image/x-icon" href="data:image/x-icon;base64,AAABAAEAEBAQAAEABAAoAQAAFgAAACgAAAAQAAAAIAAAAAEABAAAAAAAgAAAAAAAAAAAAAAAEAAAAAAAAACAAACAAAAAgIAAgAAAAIAAgACAgAAAwMDAAICAgAAAAP8AAP8AAAD//wD/AAAA/wD/AP//AAD///8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA">

      <style>
        * {
          box-sizing: border-box;
          margin: 0;
          padding: 0;
        }

        body {
          font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
          min-height: 100vh;
          padding: 20px;
          color: #333;
        }

        .container {
          max-width: 900px;
          margin: 0 auto;
          background: rgba(255, 255, 255, 0.95);
          border-radius: 20px;
          box-shadow: 0 20px 40px rgba(0,0,0,0.1);
          overflow: hidden;
          backdrop-filter: blur(10px);
        }

        .header {
          background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
          color: white;
          padding: 30px;
          text-align: center;
        }

        .header h1 {
          font-size: 2.5rem;
          margin-bottom: 10px;
          font-weight: 700;
        }

        .header p {
          font-size: 1.1rem;
          opacity: 0.9;
        }

        .main-content {
          padding: 30px;
        }

        .input-group {
          margin-bottom: 20px;
        }

        .input-label {
          display: block;
          margin-bottom: 8px;
          font-weight: 600;
          color: #555;
        }

        .url-input {
          width: 100%;
          padding: 15px 20px;
          border: 2px solid #e1e5e9;
          border-radius: 10px;
          font-size: 1rem;
          transition: all 0.3s ease;
          background: #f8f9fa;
        }

        .url-input:focus {
          outline: none;
          border-color: #667eea;
          background: white;
          box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }

        .search-button {
          width: 100%;
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
          color: white;
          padding: 15px 30px;
          border: none;
          border-radius: 10px;
          font-size: 1.1rem;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.3s ease;
          text-transform: uppercase;
          letter-spacing: 1px;
        }

        .search-button:hover {
          transform: translateY(-2px);
          box-shadow: 0 10px 20px rgba(102, 126, 234, 0.3);
        }

        .search-button:active {
          transform: translateY(0);
        }

        .search-button:disabled {
          background: #ccc;
          cursor: not-allowed;
          transform: none;
        }

        .result-section {
          margin-top: 30px;
        }

        .location-display {
          background: #f8f9fa;
          border: 2px solid #e9ecef;
          border-radius: 10px;
          padding: 20px;
          margin-bottom: 20px;
          min-height: 100px;
          display: flex;
          align-items: center;
          justify-content: center;
          text-align: center;
          font-size: 1.1rem;
          line-height: 1.5;
        }

        .location-display.loading {
          background: linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%);
          background-size: 200% 100%;
          animation: loading 1.5s infinite;
        }

        .location-display.success {
          background: #d4edda;
          border-color: #c3e6cb;
          color: #155724;
        }

        .location-display.error {
          background: #f8d7da;
          border-color: #f5c6cb;
          color: #721c24;
        }

        @keyframes loading {
          0% { background-position: 200% 0; }
          100% { background-position: -200% 0; }
        }

        .maps-link {
          display: inline-block;
          background: #4285f4;
          color: white;
          padding: 10px 20px;
          border-radius: 5px;
          text-decoration: none;
          margin-top: 10px;
          transition: background 0.3s ease;
        }

        .maps-link:hover {
          background: #3367d6;
        }

        .map-container {
          border-radius: 10px;
          overflow: hidden;
          box-shadow: 0 5px 15px rgba(0,0,0,0.1);
          height: 400px;
        }

        #map {
          height: 100%;
          width: 100%;
        }

        .status-indicator {
          display: inline-block;
          width: 8px;
          height: 8px;
          border-radius: 50%;
          margin-right: 8px;
        }

        .status-indicator.loading {
          background: #ffc107;
          animation: pulse 1.5s infinite;
        }

        .status-indicator.success {
          background: #28a745;
        }

        .status-indicator.error {
          background: #dc3545;
        }

        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }

        .footer {
          background: #f8f9fa;
          padding: 20px;
          text-align: center;
          color: #6c757d;
          font-size: 0.9rem;
        }

        @media (max-width: 768px) {
          .header h1 {
            font-size: 2rem;
          }
          
          .main-content {
            padding: 20px;
          }
          
          body {
            padding: 10px;
          }
        }
      </style>
    </head>
    <body>
      <div class="container">
        <div class="header">
          <h1>🗺️ Instagram Location Finder</h1>
          <p>Extract and discover locations from Instagram reels with precision</p>
        </div>
        
        <div class="main-content">
          <div class="input-group">
            <label class="input-label" for="reel-url">Instagram Reel URL</label>
            <input 
              type="text" 
              id="reel-url" 
              class="url-input"
              placeholder="Paste your Instagram reel URL here (e.g., https://www.instagram.com/reel/...)" 
            />
          </div>
          
          <button class="search-button" onclick="fetchLocation()" id="search-btn">
            🔍 Find Location
          </button>
          
          <div class="result-section">
            <div id="location-text" class="location-display">
              <span>🎯 Ready to find location! Paste an Instagram reel URL and click search.</span>
            </div>
            
            <div class="map-container">
              <div id="map"></div>
            </div>
          </div>
        </div>
        
        <div class="footer">
          <p>💡 Tip: Make sure the Instagram reel contains location information in its description</p>
        </div>
      </div>

      <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
      <script>
        // Initialize map
        const map = L.map('map').setView([20.5937, 78.9629], 4);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 18,
            attribution: '© OpenStreetMap contributors'
        }).addTo(map);

        let currentMarker = null;

        function updateLocationDisplay(content, type = 'default') {
          const locationDisplay = document.getElementById('location-text');
          const statusIndicator = '<span class="status-indicator ' + type + '"></span>';
          
          locationDisplay.className = 'location-display ' + type;
          locationDisplay.innerHTML = statusIndicator + content;
        }

        function updateButtonState(isLoading) {
          const button = document.getElementById('search-btn');
          const input = document.getElementById('reel-url');
          
          if (isLoading) {
            button.disabled = true;
            button.innerHTML = '🔄 Searching...';
            input.disabled = true;
          } else {
            button.disabled = false;
            button.innerHTML = '🔍 Find Location';
            input.disabled = false;
          }
        }

        function addMarkerToMap(lat, lon, name, address) {
          // Remove existing marker
          if (currentMarker) {
            map.removeLayer(currentMarker);
          }
          
          // Add new marker
          currentMarker = L.marker([lat, lon]).addTo(map);
          
          // Create popup content
          const popupContent = `
            <div style="text-align: center;">
              <h3 style="margin: 0 0 10px 0; color: #333;">${name}</h3>
              <p style="margin: 0; color: #666; font-size: 0.9rem;">${address}</p>
            </div>
          `;
          
          currentMarker.bindPopup(popupContent).openPopup();
          
          // Center map on location
          map.setView([lat, lon], 15);
        }

        async function fetchLocation() {
          const url = document.getElementById('reel-url').value.trim();
          
          if (!url) {
            updateLocationDisplay('❗ Please enter an Instagram reel URL', 'error');
            return;
          }
          
          // Validate URL format
          const instagramRegex = /instagram\.com\/(reel|p)\//;
          if (!instagramRegex.test(url)) {
            updateLocationDisplay('❗ Please enter a valid Instagram reel URL', 'error');
            return;
          }
          
          updateButtonState(true);
          updateLocationDisplay('🔍 Analyzing Instagram reel...', 'loading');
          
          try {
            const response = await fetch('/get_location', {
              method: 'POST',
              headers: { 
                'Content-Type': 'application/json' 
              },
              body: JSON.stringify({ reel_url: url })
            });
            
            const data = await response.json();
            
            if (response.ok) {
              let displayContent = data.location_text;
              
              // Add maps link if available
              if (data.maps_url) {
                displayContent += `<br><br><a href="${data.maps_url}" target="_blank" class="maps-link">🗺️ View on Google Maps</a>`;
              }
              
              // Add address if available and different from location text
              if (data.address && data.address !== data.location_text) {
                displayContent += `<br><small style="color: #666; font-style: italic;">📍 ${data.address}</small>`;
              }
              
              updateLocationDisplay(displayContent, 'success');
              
              // Add marker to map if coordinates are available
              if (data.lat && data.lon) {
                addMarkerToMap(data.lat, data.lon, 
                             data.location_text.replace('Found: ', '').replace('Found via geocoding: ', ''), 
                             data.address || 'Location found');
              } else {
                // Reset map view if no coordinates
                map.setView([20.5937, 78.9629], 4);
              }
              
            } else {
              updateLocationDisplay(`❌ ${data.error || 'Failed to find location'}`, 'error');
            }
            
          } catch (error) {
            console.error('Error:', error);
            updateLocationDisplay('🚫 Network error. Please check your connection and try again.', 'error');
          } finally {
            updateButtonState(false);
          }
        }
        
        // Allow Enter key to trigger search
        document.getElementById('reel-url').addEventListener('keypress', function(e) {
          if (e.key === 'Enter') {
            fetchLocation();
          }
        });
        
        // Auto-focus on input when page loads
        window.addEventListener('load', function() {
          document.getElementById('reel-url').focus();
        });
      </script>
    </body>
    </html>
    """

@app.route("/test")
def test():
    """Test endpoint to verify deployment and API configuration."""
    return jsonify({
        "status": "working",
        "message": "Flask app is running successfully",
        "timestamp": logger.handlers[0].formatter.formatTime(logging.LogRecord('test', logging.INFO, '', 0, '', (), None)),
        "apis_configured": {
            "serpapi": bool(SERPAPI_KEY),
            "google_maps": bool(GOOGLE_MAPS_API_KEY), 
            "opencage": bool(OPENCAGE_API_KEY),
            "spacy_model": "en_core_web_sm loaded" if 'nlp' in globals() else "not loaded"
        }
    })

@app.route("/get_location", methods=["POST"])
def get_location():
    """Main endpoint for processing Instagram reel URLs and extracting locations."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "location_text": "Invalid request format",
                "error": "JSON data required",
                "lat": None, "lon": None, "maps_url": None,
                "source": "request_error", "address": None
            }), 400
        
        reel_url = data.get("reel_url", "").strip()
        logger.info(f"Received URL: {reel_url}")

        if not reel_url:
            logger.error("No URL provided in request")
            return jsonify({
                "location_text": "No URL provided",
                "error": "Instagram reel URL is required",
                "lat": None, "lon": None, "maps_url": None,
                "source": "validation_error", "address": None
            }), 400

        # Validate URL format
        if not any(domain in reel_url for domain in ['instagram.com', 'serpapi.com']):
            return jsonify({
                "location_text": "Invalid URL format",
                "error": "Please provide a valid Instagram reel URL or SerpAPI URL",
                "lat": None, "lon": None, "maps_url": None,
                "source": "validation_error", "address": None
            }), 400

        # Check if the URL is a SerpApi URL
        serpapi_result = convert_serpapi_to_google_maps(reel_url)
        if serpapi_result[0] is not None and serpapi_result[1] is not None:
            maps_url, place_id = serpapi_result
            try:
                result = get_place_details_from_id(place_id, maps_url)
                response = {
                    "location_text": f"Found: {result['name']}",
                    "lat": result["lat"],
                    "lon": result["lon"], 
                    "maps_url": finalize_maps_url(result["maps_url"]),
                    "source": result["source"],
                    "address": result["address"]
                }
                logger.info(f"SerpAPI processing complete - map link: {response['maps_url']}")
                return jsonify(response)
                
            except Exception as e:
                logger.error(f"Error processing SerpApi URL: {str(e)}")
                return jsonify({
                    "location_text": f"Could not process SerpAPI URL",
                    "error": f"SerpAPI processing failed: {str(e)}",
                    "lat": None, "lon": None,
                    "maps_url": finalize_maps_url(maps_url),
                    "source": "serpapi_error", "address": None
                }), 422

        # Process Instagram reel
        try:
            # Extract description without downloading video to avoid Instagram restrictions
            description = extract_description(reel_url)
            
            if not description:
                return jsonify({
                    "location_text": "Could not extract description from Instagram reel",
                    "error": "Instagram may be restricting access or the reel has no description. Try a different reel.",
                    "lat": None, "lon": None, "maps_url": None,
                    "source": "extraction_failed", "address": None
                }), 422
            
            logger.info(f"Successfully extracted description: {description[:200]}...")
            
            # Parse description for location information
            lines = description.splitlines()
            location_block = None
            business_name = extract_business_name(description)
            
            # Look for location keywords
            location_keywords = ["location", "address", "place", "shop location", "📍", "🏠", "🏢", "🏪"]
            
            for i, line in enumerate(lines):
                line_lower = line.strip().lower()
                if any(keyword in line_lower for keyword in location_keywords):
                    if ":" in line:
                        location_block = line.split(":", 1)[1].strip()
                    else:
                        location_block = line.strip()
                    
                    # Collect continuation lines
                    collected_lines = []
                    for j in range(i + 1, len(lines)):
                        next_line = lines[j].strip()
                        if not next_line or next_line.startswith("#") or next_line.startswith("@"):
                            break
                        collected_lines.append(next_line)
                    
                    if collected_lines:
                        location_block += " " + " ".join(collected_lines)
                    break

            # If no explicit location block found, use NLP extraction
            if not location_block:
                logger.info("No location block found, using NLP extraction")
                location_names = extract_location_name(description)
                if location_names:
                    location_block = " ".join(location_names)
                    logger.info(f"Using NLP extracted locations: {location_block}")
                else:
                    return jsonify({
                        "location_text": "No location information found in reel description",
                        "error": "The reel description doesn't contain recognizable location information",
                        "lat": None, "lon": None, "maps_url": None,
                        "source": "no_location_found", "address": None
                    }), 422

            # Clean and standardize the location block
            cleaned_location = clean_location_block(location_block)
            
            # Search for the location using various APIs
            search_result = google_maps_search(cleaned_location, business_name)
            
            if search_result and search_result.get("lat") and search_result.get("lon"):
                final_maps_url = finalize_maps_url(search_result["maps_url"])
                response = {
                    "location_text": f"Found: {search_result['name']}",
                    "lat": search_result["lat"],
                    "lon": search_result["lon"],
                    "maps_url": final_maps_url,
                    "source": search_result["source"],
                    "address": search_result.get("address", cleaned_location)
                }
                logger.info(f"Location search successful - map link: {final_maps_url}")
                return jsonify(response)

            # Fallback to geocoding if direct search fails
            logger.info("Direct search failed, trying geocoding fallback")
            lat, lon = get_coordinates_from_address(cleaned_location)
            
            if lat and lon:
                fallback_maps_url = f"https://www.google.com/maps/search/?q={lat},{lon}&z=17"
                response = {
                    "location_text": f"Found via geocoding: {cleaned_location}",
                    "lat": lat,
                    "lon": lon,
                    "maps_url": fallback_maps_url,
                    "source": "geocoding_fallback",
                    "address": cleaned_location
                }
                logger.info(f"Geocoding fallback successful - map link: {fallback_maps_url}")
                return jsonify(response)

            # No coordinates found anywhere
            return jsonify({
                "location_text": f"Location identified but coordinates not found: {cleaned_location}",
                "error": "Could not determine precise coordinates for this location",
                "lat": None, "lon": None, "maps_url": None,
                "source": "coordinates_not_found",
                "address": cleaned_location
            }), 422

        except Exception as processing_error:
            logger.error(f"Error processing Instagram reel: {str(processing_error)}")
            return jsonify({
                "location_text": "Error processing Instagram reel",
                "error": f"Processing failed: {str(processing_error)}",
                "lat": None, "lon": None, "maps_url": None,
                "source": "processing_error", "address": None
            }), 500

    except Exception as e:
        logger.error(f"Unexpected error in get_location: {str(e)}")
        return jsonify({
            "location_text": "Internal server error",
            "error": "An unexpected error occurred. Please try again.",
            "lat": None, "lon": None, "maps_url": None,
            "source": "server_error", "address": None
        }), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)
