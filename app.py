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

# --- Core Functions (same as before) ---
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
    """Serve the main application page with FitMeal-inspired design."""
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>ReelBites | Discover Locations from Instagram</title>
      <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
      <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&family=Playfair+Display:wght@400;500;600;700&display=swap" rel="stylesheet">
      <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
      <style>
        :root {
          --primary: #4CAF50;
          --primary-dark: #388E3C;
          --secondary: #FF9800;
          --light: #F5F5F5;
          --dark: #212121;
          --gray: #757575;
          --white: #FFFFFF;
          --error: #F44336;
          --success: #4CAF50;
          --warning: #FFC107;
        }
        
        * {
          margin: 0;
          padding: 0;
          box-sizing: border-box;
        }
        
        body {
          font-family: 'Poppins', sans-serif;
          background-color: var(--light);
          color: var(--dark);
          line-height: 1.6;
        }
        
        .container {
          max-width: 1200px;
          margin: 0 auto;
          padding: 0 20px;
        }
        
        /* Header */
        header {
          background: linear-gradient(135deg, var(--primary), var(--primary-dark));
          color: var(--white);
          padding: 20px 0;
          box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
        }
        
        .header-content {
          display: flex;
          justify-content: space-between;
          align-items: center;
        }
        
        .logo {
          font-family: 'Playfair Display', serif;
          font-size: 28px;
          font-weight: 700;
          display: flex;
          align-items: center;
        }
        
        .logo i {
          margin-right: 10px;
          color: var(--secondary);
        }
        
        nav ul {
          display: flex;
          list-style: none;
        }
        
        nav ul li {
          margin-left: 30px;
        }
        
        nav ul li a {
          color: var(--white);
          text-decoration: none;
          font-weight: 500;
          transition: all 0.3s ease;
        }
        
        nav ul li a:hover {
          color: var(--secondary);
        }
        
        /* Hero Section */
        .hero {
          background: url('https://images.unsplash.com/photo-1490645935967-10de6ba17061?ixlib=rb-1.2.1&auto=format&fit=crop&w=1350&q=80') no-repeat center center/cover;
          height: 500px;
          display: flex;
          align-items: center;
          position: relative;
        }
        
        .hero::before {
          content: '';
          position: absolute;
          top: 0;
          left: 0;
          width: 100%;
          height: 100%;
          background: rgba(0, 0, 0, 0.5);
        }
        
        .hero-content {
          position: relative;
          z-index: 1;
          color: var(--white);
          max-width: 600px;
        }
        
        .hero h1 {
          font-family: 'Playfair Display', serif;
          font-size: 48px;
          font-weight: 700;
          margin-bottom: 20px;
          line-height: 1.2;
        }
        
        .hero p {
          font-size: 18px;
          margin-bottom: 30px;
        }
        
        /* Search Section */
        .search-section {
          background-color: var(--white);
          padding: 60px 0;
          margin-top: -80px;
          border-radius: 10px;
          box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);
          position: relative;
          z-index: 2;
        }
        
        .search-container {
          max-width: 800px;
          margin: 0 auto;
          padding: 0 20px;
        }
        
        .search-box {
          background-color: var(--white);
          border-radius: 8px;
          padding: 40px;
          box-shadow: 0 5px 15px rgba(0, 0, 0, 0.05);
        }
        
        .search-title {
          text-align: center;
          margin-bottom: 30px;
        }
        
        .search-title h2 {
          font-family: 'Playfair Display', serif;
          font-size: 32px;
          color: var(--dark);
          margin-bottom: 10px;
        }
        
        .search-title p {
          color: var(--gray);
        }
        
        .search-form {
          display: flex;
          flex-direction: column;
        }
        
        .form-group {
          margin-bottom: 20px;
        }
        
        .form-group label {
          display: block;
          margin-bottom: 8px;
          font-weight: 500;
          color: var(--dark);
        }
        
        .form-control {
          width: 100%;
          padding: 15px 20px;
          border: 2px solid #e0e0e0;
          border-radius: 8px;
          font-size: 16px;
          transition: all 0.3s ease;
        }
        
        .form-control:focus {
          outline: none;
          border-color: var(--primary);
          box-shadow: 0 0 0 3px rgba(76, 175, 80, 0.2);
        }
        
        .btn {
          display: inline-block;
          background-color: var(--primary);
          color: var(--white);
          border: none;
          border-radius: 8px;
          padding: 15px 30px;
          font-size: 16px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.3s ease;
          text-align: center;
          text-transform: uppercase;
          letter-spacing: 1px;
        }
        
        .btn:hover {
          background-color: var(--primary-dark);
          transform: translateY(-2px);
          box-shadow: 0 5px 15px rgba(0, 0, 0, 0.1);
        }
        
        .btn:active {
          transform: translateY(0);
        }
        
        .btn-block {
          display: block;
          width: 100%;
        }
        
        /* Results Section */
        .results-section {
          padding: 60px 0;
        }
        
        .results-container {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 30px;
        }
        
        @media (max-width: 768px) {
          .results-container {
            grid-template-columns: 1fr;
          }
        }
        
        .location-card {
          background-color: var(--white);
          border-radius: 8px;
          padding: 30px;
          box-shadow: 0 5px 15px rgba(0, 0, 0, 0.05);
        }
        
        .location-card h3 {
          font-family: 'Playfair Display', serif;
          font-size: 24px;
          margin-bottom: 15px;
          color: var(--dark);
        }
        
        .location-info {
          margin-bottom: 20px;
        }
        
        .location-info p {
          margin-bottom: 10px;
        }
        
        .location-address {
          color: var(--gray);
          font-style: italic;
        }
        
        .map-container {
          height: 400px;
          border-radius: 8px;
          overflow: hidden;
          box-shadow: 0 5px 15px rgba(0, 0, 0, 0.05);
        }
        
        #map {
          height: 100%;
          width: 100%;
        }
        
        .action-buttons {
          display: flex;
          gap: 15px;
          margin-top: 20px;
        }
        
        .btn-secondary {
          background-color: var(--secondary);
        }
        
        .btn-secondary:hover {
          background-color: #F57C00;
        }
        
        /* Status Indicators */
        .status {
          display: flex;
          align-items: center;
          margin-bottom: 20px;
          padding: 15px;
          border-radius: 8px;
        }
        
        .status i {
          margin-right: 10px;
          font-size: 20px;
        }
        
        .status.loading {
          background-color: rgba(255, 193, 7, 0.1);
          color: #FFA000;
        }
        
        .status.success {
          background-color: rgba(76, 175, 80, 0.1);
          color: var(--success);
        }
        
        .status.error {
          background-color: rgba(244, 67, 54, 0.1);
          color: var(--error);
        }
        
        /* Footer */
        footer {
          background-color: var(--dark);
          color: var(--white);
          padding: 40px 0 20px;
        }
        
        .footer-content {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: 30px;
          margin-bottom: 30px;
        }
        
        .footer-column h4 {
          font-family: 'Playfair Display', serif;
          font-size: 18px;
          margin-bottom: 20px;
          position: relative;
          padding-bottom: 10px;
        }
        
        .footer-column h4::after {
          content: '';
          position: absolute;
          left: 0;
          bottom: 0;
          width: 50px;
          height: 2px;
          background-color: var(--primary);
        }
        
        .footer-column ul {
          list-style: none;
        }
        
        .footer-column ul li {
          margin-bottom: 10px;
        }
        
        .footer-column ul li a {
          color: #BDBDBD;
          text-decoration: none;
          transition: all 0.3s ease;
        }
        
        .footer-column ul li a:hover {
          color: var(--white);
          padding-left: 5px;
        }
        
        .social-links {
          display: flex;
          gap: 15px;
          margin-top: 20px;
        }
        
        .social-links a {
          display: flex;
          align-items: center;
          justify-content: center;
          width: 40px;
          height: 40px;
          background-color: rgba(255, 255, 255, 0.1);
          border-radius: 50%;
          color: var(--white);
          transition: all 0.3s ease;
        }
        
        .social-links a:hover {
          background-color: var(--primary);
          transform: translateY(-3px);
        }
        
        .copyright {
          text-align: center;
          padding-top: 20px;
          border-top: 1px solid rgba(255, 255, 255, 0.1);
          color: #BDBDBD;
          font-size: 14px;
        }
        
        /* Responsive */
        @media (max-width: 768px) {
          .header-content {
            flex-direction: column;
            text-align: center;
          }
          
          nav ul {
            margin-top: 20px;
          }
          
          nav ul li {
            margin: 0 10px;
          }
          
          .hero h1 {
            font-size: 36px;
          }
          
          .hero p {
            font-size: 16px;
          }
          
          .search-box {
            padding: 20px;
          }
        }
      </style>
    </head>
    <body>
      <!-- Header -->
      <header>
        <div class="container header-content">
          <div class="logo">
            <i class="fas fa-map-marker-alt"></i>
            <span>ReelBites</span>
          </div>
          <nav>
            <ul>
              <li><a href="#">Home</a></li>
              <li><a href="#">How It Works</a></li>
              <li><a href="#">About</a></li>
              <li><a href="#">Contact</a></li>
            </ul>
          </nav>
        </div>
      </header>
      
      <!-- Hero Section -->
      <section class="hero">
        <div class="container hero-content">
          <h1>Discover Amazing Locations From Instagram</h1>
          <p>Find any place featured in Instagram reels with our powerful location finder tool. Perfect for foodies, travelers, and explorers.</p>
        </div>
      </section>
      
      <!-- Search Section -->
      <section class="search-section">
        <div class="search-container">
          <div class="search-box">
            <div class="search-title">
              <h2>Find a Location</h2>
              <p>Paste an Instagram reel URL below to discover the location</p>
            </div>
            
            <div class="search-form">
              <div class="form-group">
                <label for="reel-url">Instagram Reel URL</label>
                <input 
                  type="text" 
                  id="reel-url" 
                  class="form-control"
                  placeholder="https://www.instagram.com/reel/..." 
                />
              </div>
              
              <button class="btn btn-block" onclick="fetchLocation()" id="search-btn">
                <i class="fas fa-search"></i> Find Location
              </button>
            </div>
          </div>
        </div>
      </section>
      
      <!-- Results Section -->
      <section class="results-section">
        <div class="container">
          <div id="status-container"></div>
          
          <div class="results-container">
            <div class="location-card">
              <h3>Location Details</h3>
              <div class="location-info" id="location-text">
                <p>Ready to find locations! Paste an Instagram reel URL and click search.</p>
              </div>
              
              <div class="action-buttons" id="action-buttons" style="display: none;">
                <a href="#" class="btn" id="maps-link" target="_blank">
                  <i class="fas fa-map-marked-alt"></i> View on Maps
                </a>
                <button class="btn btn-secondary" onclick="copyToClipboard()">
                  <i class="fas fa-copy"></i> Copy Address
                </button>
              </div>
            </div>
            
            <div class="map-container">
              <div id="map"></div>
            </div>
          </div>
        </div>
      </section>
      
      <!-- Footer -->
      <footer>
        <div class="container">
          <div class="footer-content">
            <div class="footer-column">
              <h4>ReelBites</h4>
              <p>Discover amazing locations from Instagram reels with our powerful location finder tool.</p>
              <div class="social-links">
                <a href="#"><i class="fab fa-facebook-f"></i></a>
                <a href="#"><i class="fab fa-twitter"></i></a>
                <a href="#"><i class="fab fa-instagram"></i></a>
                <a href="#"><i class="fab fa-linkedin-in"></i></a>
              </div>
            </div>
            
            <div class="footer-column">
              <h4>Quick Links</h4>
              <ul>
                <li><a href="#">Home</a></li>
                <li><a href="#">About Us</a></li>
                <li><a href="#">How It Works</a></li>
                <li><a href="#">Contact</a></li>
              </ul>
            </div>
            
            <div class="footer-column">
              <h4>Support</h4>
              <ul>
                <li><a href="#">FAQ</a></li>
                <li><a href="#">Privacy Policy</a></li>
                <li><a href="#">Terms of Service</a></li>
                <li><a href="#">Help Center</a></li>
              </ul>
            </div>
            
            <div class="footer-column">
              <h4>Contact Us</h4>
              <ul>
                <li><i class="fas fa-map-marker-alt"></i> 123 Street, Kochi, India</li>
                <li><i class="fas fa-phone"></i> +91 1234567890</li>
                <li><i class="fas fa-envelope"></i> info@ReelBites.com</li>
              </ul>
            </div>
          </div>
          
          <div class="copyright">
            <p>&copy; 2023 ReelBites. All Rights Reserved.</p>
          </div>
        </div>
      </footer>
      
      <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
      <script>
        // Initialize map centered on India
        const map = L.map('map').setView([20.5937, 78.9629], 5);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        }).addTo(map);
        
        let currentMarker = null;
        let currentLocationData = null;
        
        // Update status display
        function updateStatus(message, type) {
          const statusContainer = document.getElementById('status-container');
          let icon = '';
          
          if (type === 'loading') {
            icon = '<i class="fas fa-spinner fa-spin"></i>';
          } else if (type === 'success') {
            icon = '<i class="fas fa-check-circle"></i>';
          } else if (type === 'error') {
            icon = '<i class="fas fa-exclamation-circle"></i>';
          }
          
          statusContainer.innerHTML = `
            <div class="status ${type}">
              ${icon}
              <span>${message}</span>
            </div>
          `;
        }
        
        // Update location display
        function updateLocationDisplay(content, address = null) {
          const locationDisplay = document.getElementById('location-text');
          const actionButtons = document.getElementById('action-buttons');
          
          if (address) {
            locationDisplay.innerHTML = `
              <h3>${content}</h3>
              <p class="location-address">${address}</p>
            `;
            actionButtons.style.display = 'flex';
          } else {
            locationDisplay.innerHTML = `<p>${content}</p>`;
            actionButtons.style.display = 'none';
          }
        }
        
        // Update button state during loading
        function updateButtonState(isLoading) {
          const button = document.getElementById('search-btn');
          
          if (isLoading) {
            button.disabled = true;
            button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Searching...';
          } else {
            button.disabled = false;
            button.innerHTML = '<i class="fas fa-search"></i> Find Location';
          }
        }
        
        // Add marker to map
        function addMarkerToMap(lat, lon, name, address) {
          // Remove existing marker
          if (currentMarker) {
            map.removeLayer(currentMarker);
          }
          
          // Add new marker with custom icon
          const greenIcon = L.icon({
            iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-green.png',
            shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
            iconSize: [25, 41],
            iconAnchor: [12, 41],
            popupAnchor: [1, -34],
            shadowSize: [41, 41]
          });
          
          currentMarker = L.marker([lat, lon], {icon: greenIcon}).addTo(map);
          
          // Create popup content
          const popupContent = `
            <div style="text-align: center;">
              <h3 style="margin: 0 0 10px 0; color: #333;">${name}</h3>
              <p style="margin: 0; color: #666; font-size: 0.9rem;">${address}</p>
            </div>
          `;
          
          currentMarker.bindPopup(popupContent).openPopup();
          
          // Center map on location with smooth zoom
          map.flyTo([lat, lon], 15, {
            duration: 1,
            easeLinearity: 0.25
          });
        }
        
        // Copy address to clipboard
        function copyToClipboard() {
          if (!currentLocationData) return;
          
          const text = `${currentLocationData.location_text}\n${currentLocationData.address}`;
          navigator.clipboard.writeText(text)
            .then(() => {
              alert('Address copied to clipboard!');
            })
            .catch(err => {
              console.error('Could not copy text: ', err);
            });
        }
        
        // Main function to fetch location
        async function fetchLocation() {
          const url = document.getElementById('reel-url').value.trim();
          
          if (!url) {
            updateStatus('Please enter an Instagram reel URL', 'error');
            return;
          }
          
          // Validate URL format
          const instagramRegex = /instagram\.com\/(reel|p)\//;
          if (!instagramRegex.test(url)) {
            updateStatus('Please enter a valid Instagram reel URL', 'error');
            return;
          }
          
          updateButtonState(true);
          updateStatus('Analyzing Instagram reel...', 'loading');
          updateLocationDisplay('Searching for location information...');
          
          try {
            const response = await fetch('/get_location', {
              method: 'POST',
              headers: { 
                'Content-Type': 'application/json' 
              },
              body: JSON.stringify({ reel_url: url })
            });
            
            const data = await response.json();
            currentLocationData = data;
            
            if (response.ok) {
              if (data.lat && data.lon) {
                // Successful location with coordinates
                updateStatus('Location found successfully!', 'success');
                updateLocationDisplay(data.location_text.replace('Found: ', '').replace('Found via geocoding: ', ''), 
                               data.address || data.location_text);
                
                // Update maps link button
                if (data.maps_url) {
                  const mapsLink = document.getElementById('maps-link');
                  mapsLink.href = data.maps_url;
                }
                
                // Add marker to map
                addMarkerToMap(data.lat, data.lon, 
                             data.location_text.replace('Found: ', '').replace('Found via geocoding: ', ''), 
                             data.address || 'Location found');
              } else {
                // Location found but no coordinates
                updateStatus('Location identified but no precise coordinates found', 'warning');
                updateLocationDisplay(data.location_text, data.address || data.location_text);
                
                // Reset map view
                map.setView([20.5937, 78.9629], 5);
              }
            } else {
              // Error from server
              updateStatus(data.error || 'Failed to find location', 'error');
              updateLocationDisplay(data.error || 'Failed to find location');
              
              // Reset map view
              map.setView([20.5937, 78.9629], 5);
            }
          } catch (error) {
            console.error('Error:', error);
            updateStatus('Network error. Please check your connection and try again.', 'error');
            updateLocationDisplay('Network error. Please try again.');
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