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

# --- Config ---
SERPAPI_KEY = "9184855c3a7f4401806ebdc8ba1c35bf169b449c808d6bf9baca859376d1b4e5"
GOOGLE_MAPS_API_KEY = "AIzaSyC9v39XIK9P-uzJCmvAN1OK7AUGyvxZUH0"
OPENCAGE_API_KEY = "048fafc1e4cf46e7adc98464f21bcae5"
app = Flask(__name__)

# Handle SpaCy model for Vercel
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    import sys, subprocess
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
            logger.info(f"---------------------\n*** FALLBACK GOOGLE MAPS LINK ***\n{fallback_maps_url}\n---------------------")
            return {
                "name": "Unknown Place",
                "address": "Unknown Address",
                "lat": None,
                "lon": None,
                "maps_url": fallback_maps_url,
                "source": "google_maps_api_place_id_fallback"
            }
    except requests.RequestException as e:
        logger.error(f"Google Maps API request failed for place_id {place_id}: {e}")
        logger.info(f"---------------------\n*** FALLBACK GOOGLE MAPS LINK ***\n{fallback_maps_url}\n---------------------")
        return {
            "name": "Unknown Place",
            "address": "Unknown Address",
            "lat": None,
            "lon": None,
            "maps_url": fallback_maps_url,
            "source": "google_maps_api_error"
        }
    except Exception as e:
        logger.error(f"Error fetching place details for place_id {place_id}: {e}")
        logger.info(f"---------------------\n*** FALLBACK GOOGLE MAPS LINK ***\n{fallback_maps_url}\n---------------------")
        return {
            "name": "Unknown Place",
            "address": "Unknown Address",
            "lat": None,
            "lon": None,
            "maps_url": fallback_maps_url,
            "source": "google_maps_api_error"
        }

def download_reel(url, output_path):
    """Download Instagram reel using yt-dlp."""
    try:
        cmd = ["yt-dlp", "-f", "best[ext=mp4]", "-o", output_path, url]
        subprocess.run(cmd, check=True, text=True)
        logger.info(f"Downloaded reel to {output_path}")
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to download reel: {e}")
        raise

def extract_description(url):
    """Extract reel description using yt-dlp."""
    try:
        cmd = ['yt-dlp', '--dump-json', url]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            logger.error(f"yt-dlp error: {result.stderr}")
            return ""
        info = json.loads(result.stdout)
        return info.get("description", "")
    except Exception as e:
        logger.error(f"Error extracting description: {e}")
        return ""

def extract_location_name(text):
    """Extract location names using SpaCy."""
    doc = nlp(text)
    locations = [ent.text for ent in doc.ents if ent.label_ in ["GPE", "FAC", "ORG", "LOC"]]
    logger.info(f"Extracted location names: {locations}")
    return locations

def extract_business_name(text):
    """Extract business name or Instagram handle."""
    match = re.search(r'@\w+', text)
    if match:
        business = match.group(0).lstrip('@')
        logger.info(f"Extracted business name from handle: {business}")
        return business
    doc = nlp(text)
    for ent in doc.ents:
        if ent.label_ in ["ORG", "FAC"]:
            logger.info(f"Extracted business name from NLP: {ent.text}")
            return ent.text
    return None

def clean_location_block(location_block):
    """Clean and standardize location block."""
    if not location_block:
        return ""
    cleaned = re.sub(r'@\w+\.\w+', '', location_block).strip()
    cleaned = re.sub(r'@\w+', '', cleaned).strip()
    cleaned = re.sub(r'371302', '682025', cleaned).strip()
    cleaned = re.sub(r'\s*,\s*', ', ', cleaned).strip(', ')
    if "Kochi" not in cleaned and "Kerala" not in cleaned:
        cleaned += ", Kochi, Kerala, 682025"
    elif "Kochi" in cleaned and "Kerala" not in cleaned:
        cleaned += ", Kerala, 682025"
    elif "Kerala" in cleaned and "682025" not in cleaned:
        cleaned += ", 682025"
    logger.info(f"Cleaned location block: {cleaned}")
    return cleaned

def google_maps_search(query, business_name=None):
    """Search for location using Google Maps Places API with SerpAPI fallback."""
    search_query = f"{business_name}, {query}" if business_name else query
    logger.info(f"Searching Google Maps for: {search_query}")
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
        response = requests.post(url, json=payload, headers=headers)
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
            logger.warning(f"Google Maps API failed: {results.get('status', 'No status')}")
    except Exception as e:
        logger.error(f"Google Maps API search failed: {e}")

    # Fallback to SerpAPI
    try:
        params = {
            "engine": "google_maps",
            "q": search_query,
            "ll": "@9.931233,76.267304,15z",
            "type": "search",
            "api_key": SERPAPI_KEY
        }
        search = GoogleSearch(params)
        results = search.get_dict()
        logger.info(f"SerpAPI results: {results}")

        if "place_results" in results:
            place = results["place_results"]
            lat = place.get("gps_coordinates", {}).get("latitude")
            lon = place.get("gps_coordinates", {}).get("longitude")
            name = place.get("title", search_query)
            maps_url = place.get("place_id_search") or f"https://www.google.com/maps/search/{quote(name)}/@{lat},{lon},17z"
            return {
                "name": name,
                "address": place.get("address", query),
                "lat": lat,
                "lon": lon,
                "maps_url": maps_url,
                "source": "google_maps"
            }
        elif "local_results" in results and results["local_results"]:
            place = results["local_results"][0]
            lat = place.get("gps_coordinates", {}).get("latitude")
            lon = place.get("gps_coordinates", {}).get("longitude")
            name = place.get("title", search_query)
            maps_url = place.get("links", {}).get("place_results") or f"https://www.google.com/maps/search/{quote(name)}/@{lat},{lon},17z"
            return {
                "name": name,
                "address": place.get("address", query),
                "lat": lat,
                "lon": lon,
                "maps_url": maps_url,
                "source": "google_maps"
            }
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
    try:
        address_parts = address.split(", ")
        refined_address = ", ".join(part for part in address_parts if part not in ["India", "Ernakulam"])
        if "Kochi" not in refined_address and "Kerala" not in refined_address:
            refined_address += ", Kochi, Kerala, 682025"
        response = requests.get("https://api.opencagedata.com/geocode/v1/json", params={
            "q": refined_address,
            "key": OPENCAGE_API_KEY,
            "limit": 1,
            "countrycode": "in"
        })
        data = response.json()
        logger.info(f"OpenCage result: {data}")
        if data["results"]:
            lat = data["results"][0]["geometry"]["lat"]
            lon = data["results"][0]["geometry"]["lng"]
            return lat, lon
        logger.warning("OpenCage found no results, trying Google Maps geocoding")
    except Exception as e:
        logger.error(f"OpenCage error: {e}")

    # Fallback to Google Maps API
    try:
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={quote(refined_address)}&key={GOOGLE_MAPS_API_KEY}"
        response = requests.get(url)
        data = response.json()
        logger.info(f"Google Maps Geocoding result: {data}")
        if data["status"] == "OK" and data["results"]:
            lat = data["results"][0]["geometry"]["location"]["lat"]
            lon = data["results"][0]["geometry"]["location"]["lng"]
            return lat, lon
        return None, None
    except Exception as e:
        logger.error(f"Google Maps Geocoding error: {e}")
        return None, None

# --- Flask Routes ---

@app.route("/")
def index():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
      <title>Instagram Reel Location Finder</title>
      <link rel="stylesheet" href="https://unpkg.com/leaflet/dist/leaflet.css" />

      <style>
        body {
          font-family: Arial, sans-serif;
          background: #fdfaf6;
          margin: 0;
          padding: 20px;
        }

        h1 {
          text-align: center;
          color: #444;
        }

        .container {
          max-width: 800px;
          margin: auto;
          background: white;
          padding: 20px;
          border-radius: 12px;
          box-shadow: 0 0 10px rgba(0,0,0,0.1);
        }

        input[type="text"] {
          width: 100%;
          padding: 10px;
          margin-bottom: 10px;
          border: 1px solid #ccc;
          border-radius: 6px;
        }

        button {
          background-color: #28a745;
          color: white;
          padding: 10px 15px;
          border: none;
          border-radius: 6px;
          cursor: pointer;
        }

        #map {
          height: 400px;
          margin-top: 20px;
          border-radius: 8px;
        }

        #location-text {
          margin-top: 15px;
          padding: 10px;
          background: #f1f1f1;
          border-radius: 6px;
          font-size: 1rem;
          color: #333;
        }
      </style>
    </head>
    <body>
      <div class="container">
        <h1>Instagram Reel Location Finder</h1>
        <input type="text" id="reel-url" placeholder="Paste Instagram Reel URL here" />
        <button onclick="fetchLocation()">Find Location</button>
        
        <div id="location-text">Location will appear here...</div>
        <div id="map"></div>
      </div>

      <script src="https://unpkg.com/leaflet/dist/leaflet.js"></script>
      <script>
        const map = L.map('map').setView([20.5937, 78.9629], 4);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 18,
        }).addTo(map);

        let marker;

        function fetchLocation() {
            const url = document.getElementById('reel-url').value;
            const locationText = document.getElementById('location-text');
            locationText.innerHTML = "Searching..."; // Clear previous content
            
            fetch('/get_location', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ reel_url: url })
            })
            .then(res => res.json())
            .then(data => {
                let displayText = data.location_text;
                
                // Add maps link if available
                if (data.maps_url) {
                    displayText += `<br><a href="${data.maps_url}" target="_blank">View on Google Maps</a>`;
                }
                
                // Add address if available
                if (data.address) {
                    displayText += `<br>${data.address}`;
                }
                
                locationText.innerHTML = displayText;

                if (marker) map.removeLayer(marker);
                
                if (data.lat && data.lon) {
                    marker = L.marker([data.lat, data.lon]).addTo(map)
                        .bindPopup(data.location_text)
                        .openPopup();
                    map.setView([data.lat, data.lon], 15);
                } else if (data.maps_url) {
                    // If no coordinates but have maps link, show a message
                    locationText.innerHTML += "<br>Could not get precise coordinates, but here's the map link";
                }
            })
            .catch(err => {
                locationText.innerHTML = "Failed to fetch location.";
                console.error(err);
            });
        }
      </script>
    </body>
    </html>
    """

@app.route("/get_location", methods=["POST"])
def get_location():
    data = request.get_json()
    reel_url = data.get("reel_url", "")
    logger.info(f"Received URL: {reel_url}")

    if not reel_url:
        logger.error("No URL provided")
        response = {
            "location_text": "No URL provided",
            "error": "URL is required",
            "lat": None,
            "lon": None,
            "maps_url": None,
            "source": "error",
            "address": None
        }
        response['maps_url'] = finalize_maps_url(response['maps_url'])
        logger.info(f"map-link: {response['maps_url']}")
        return jsonify(response)

    # Check if the URL is a SerpApi URL
    serpapi_result = convert_serpapi_to_google_maps(reel_url)
    if serpapi_result is not None and serpapi_result[1] is not None:
        maps_url, place_id = serpapi_result
        try:
            result = get_place_details_from_id(place_id, maps_url)
            response = {
                "location_text": f"Found: {result['name']}",
                "lat": result["lat"],
                "lon": result["lon"],
                "maps_url": result["maps_url"],
                "source": result["source"],
                "address": result["address"]
            }
        except Exception as e:
            logger.error(f"Error processing SerpApi URL: {str(e)}")
            response = {
                "location_text": f"Could not find place details for place_id: {place_id}",
                "error": str(e),
                "lat": None,
                "lon": None,
                "maps_url": maps_url,
                "source": "serpapi_conversion",
                "address": None
            }
        response['maps_url'] = finalize_maps_url(response['maps_url'])
        logger.info(f"map-link: {response['maps_url']}")
        return jsonify(response)

    # Proceed with Instagram reel processing
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            video_path = os.path.join(tmpdir, "reel.mp4")
            download_reel(reel_url, video_path)
            description = extract_description(reel_url)
            combined_text = description
            logger.info(f"Description text: {combined_text}")

            lines = combined_text.splitlines()
            location_block = None
            business_name = extract_business_name(combined_text)
            location_keywords = ["location", "address", "place", "shop location", "📍"]
            for i, line in enumerate(lines):
                line_lower = line.strip().lower()
                if any(keyword in line_lower for keyword in location_keywords):
                    if ":" in line:
                        location_block = line.split(":", 1)[1].strip()
                    else:
                        location_block = line.strip()
                    collected = []
                    for j in range(i + 1, len(lines)):
                        next_line = lines[j].strip()
                        if not next_line or next_line.startswith("#") or next_line.startswith("@"):
                            break
                        collected.append(next_line)
                    if collected:
                        location_block += " " + " ".join(collected)
                    break

            if not location_block:
                logger.info("No location block found, using NLP")
                location_names = extract_location_name(combined_text)
                if location_names:
                    location_block = " ".join(location_names)
                    logger.info(f"Using NLP extracted locations: {location_block}")
                else:
                    response = {
                        "location_text": "No location found in description",
                        "lat": None,
                        "lon": None,
                        "maps_url": None,
                        "source": "not_found",
                        "address": None
                    }
                    response['maps_url'] = finalize_maps_url(response['maps_url'])
                    logger.info(f"map-link: {response['maps_url']}")
                    return jsonify(response)

            location_block = clean_location_block(location_block)
            result = google_maps_search(location_block, business_name)

            if result and result.get("lat") and result.get("lon"):
                lat, lon = result["lat"], result["lon"]
                name = result["name"]
                maps_url = result.get("maps_url") or f"https://www.google.com/maps/search/?q={lat},{lon}&z=17"
                response = {
                    "location_text": f"Found: {result['name']}",
                    "lat": lat,
                    "lon": lon,
                    "maps_url": maps_url,
                    "source": result["source"],
                    "address": result.get("address", location_block)
                }
                response['maps_url'] = finalize_maps_url(response['maps_url'])
                logger.info(f"map-link: {maps_url}")
                return jsonify(response)

            # Fallback to geocoding
            lat, lon = get_coordinates_from_address(location_block)
            if lat and lon:
                maps_url = f"https://www.google.com/maps/search/?q={lat},{lon}&z=17"
                response = {
                    "location_text": f"Found via geocoding: {location_block}",
                    "lat": lat,
                    "lon": lon,
                    "maps_url": maps_url,
                    "source": "geocoding",
                    "address": location_block
                }
                response['maps_url'] = finalize_maps_url(response['maps_url'])
                logger.info(f"map-link: {maps_url}")
                return jsonify(response)

            response = {
                "location_text": f"Could not find precise location for: {location_block}",
                "lat": None,
                "lon": None,
                "maps_url": None,
                "source": "no_match",
                "address": location_block
            }
            response['maps_url'] = finalize_maps_url(response['maps_url'])
            logger.info(f"map-link: {response['maps_url']}")
            return jsonify(response)

        except Exception as e:
            logger.error(f"Error occurred: {str(e)}")
            response = {
                "location_text": "Internal error",
                "error": str(e),
                "lat": None,
                "lon": None,
                "maps_url": None,
                "source": "error",
                "address": None
            }
            response['maps_url'] = finalize_maps_url(response['maps_url'])
            logger.info(f"map-link: {response['maps_url']}")
            return jsonify(response)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)