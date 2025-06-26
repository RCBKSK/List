
from flask import Flask, request, jsonify, render_template, send_file
from flask_cors import CORS
import google.generativeai as genai
from PIL import Image
import io
import base64
import json
import pandas as pd
import os
from datetime import datetime
import tempfile
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urlparse
import uuid
import hashlib
import time
from datetime import datetime, timedelta
import json
import random

app = Flask(__name__)
CORS(app)

# Configure Gemini AI (will be set by user input)
genai_api_key = None

# HSN Code to GST Rate mapping (sample data - can be expanded)
HSN_GST_MAPPING = {
    # Food items (0% - 5%)
    "0101": 0.0,   # Live horses, asses, mules and hinnies
    "0102": 0.0,   # Live bovine animals
    "0701": 0.0,   # Potatoes, fresh or chilled
    "0702": 0.0,   # Tomatoes, fresh or chilled
    "1001": 0.0,   # Wheat and meslin
    "1006": 0.0,   # Rice
    "1701": 0.05,  # Cane or beet sugar
    
    # Textiles (5% - 12%)
    "5201": 0.05,  # Cotton, not carded or combed
    "5208": 0.05,  # Woven fabrics of cotton
    "6101": 0.12,  # Men's or boys' overcoats
    "6201": 0.12,  # Women's or girls' overcoats
    "6301": 0.05,  # Blankets and travelling rugs
    "6302": 0.05,  # Bed linen, table linen
    
    # Electronics (18% - 28%)
    "8471": 0.18,  # Automatic data processing machines
    "8517": 0.18,  # Telephone sets, mobile phones
    "8528": 0.18,  # Monitors and projectors
    "8544": 0.18,  # Insulated wire, cable
    "9013": 0.18,  # Liquid crystal devices
    
    # Automobiles (28%)
    "8703": 0.28,  # Motor cars and other motor vehicles
    "8704": 0.28,  # Motor vehicles for transport of goods
    "8711": 0.28,  # Motorcycles
    
    # Chemicals (5% - 18%)
    "2501": 0.05,  # Salt
    "2804": 0.05,  # Hydrogen, rare gases
    "3004": 0.12,  # Medicaments
    "3303": 0.18,  # Perfumes and toilet waters
    "3401": 0.18,  # Soap
    
    # Books and stationery (0% - 12%)
    "4901": 0.0,   # Printed books, brochures
    "4902": 0.0,   # Newspapers, journals
    "4910": 0.05,  # Calendars of any kind
    "4911": 0.12,  # Other printed matter
    
    # Common HSN codes for various products
    "3926": 0.18,  # Other articles of plastics
    "4202": 0.18,  # Trunks, suit-cases, handbags
    "6403": 0.18,  # Footwear with outer soles of rubber
    "7013": 0.18,  # Glassware of a kind used for table
    "7323": 0.18,  # Table, kitchen or other household articles
    "8302": 0.18,  # Base metal mountings, fittings
    "8443": 0.18,  # Printing machinery
    "9403": 0.12,  # Other furniture and parts thereof
    "9404": 0.12,  # Mattress supports; articles of bedding
    "9405": 0.12,  # Lamps and lighting fittings
    "9503": 0.12,  # Tricycles, scooters, pedal cars (toys)
    "9504": 0.28,  # Video game consoles and machines
    "9505": 0.12,  # Festive, carnival or other entertainment articles
    
    # Default/Unknown HSN codes
    "9999": 0.18,  # Default rate for unknown items
}

def get_gst_rate_from_hsn_api(hsn_code):
    """
    Get GST rate from ClearTax Algolia API
    Returns tuple: (gst_rate, description, hsn_data)
    """
    if not hsn_code:
        return 0.18, "Default GST rate (18%)", None
    
    hsn_clean = str(hsn_code).replace(" ", "").strip()
    
    try:
        # ClearTax Algolia API request
        api_url = "https://cleartax.in/f/content_search/algolia/algolia-search/"
        
        payload = {
            "requests": [
                {
                    "indexName": "HSN_SAC_2021",
                    "params": f"query={hsn_clean}&optionalWords={hsn_clean}&highlightPreTag=<strong>&highlightPostTag=</strong>&typoTolerance=false"
                }
            ]
        }
        
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        response = requests.post(api_url, json=payload, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            results = data.get('results', [])
            
            if results and len(results) > 0:
                hits = results[0].get('hits', [])
                
                if hits:
                    # Find exact or best match
                    best_match = None
                    exact_match = None
                    
                    for hit in hits:
                        hit_hsn = hit.get('product_hsn_code', '')
                        
                        # Check for exact match
                        if hit_hsn == hsn_clean or hit_hsn.startswith(hsn_clean):
                            exact_match = hit
                            break
                        
                        # Keep track of best partial match
                        if not best_match and hsn_clean in hit_hsn:
                            best_match = hit
                    
                    # Use exact match if found, otherwise best match
                    selected_hit = exact_match or best_match or hits[0]
                    
                    product_rate = selected_hit.get('product_rate', '18%')
                    product_description = selected_hit.get('product_description', '')
                    chapter_name = selected_hit.get('chapter_name', '')
                    product_hsn_code = selected_hit.get('product_hsn_code', hsn_clean)
                    
                    # Extract GST rate percentage
                    gst_percentage = float(product_rate.replace('%', '')) if product_rate.replace('%', '').replace('.', '').isdigit() else 18.0
                    gst_rate = gst_percentage / 100
                    
                    description = f"HSN {product_hsn_code}: {gst_percentage}% GST - {chapter_name}"
                    
                    return gst_rate, description, {
                        'hsnCode': product_hsn_code,
                        'gstRate': gst_percentage,
                        'description': product_description,
                        'chapterName': chapter_name,
                        'source': 'ClearTax API'
                    }
    
    except Exception as e:
        print(f"Error fetching from ClearTax API: {e}")
    
    # Fallback to local mapping if API fails
    return get_gst_rate_from_hsn_local(hsn_code)

def get_gst_rate_from_hsn_local(hsn_code):
    """
    Get GST rate based on local HSN code mapping (fallback)
    Returns tuple: (gst_rate, description, hsn_data)
    """
    if not hsn_code:
        return 0.18, "Default GST rate (18%)", None
    
    # Clean HSN code - remove spaces and convert to string
    hsn_clean = str(hsn_code).replace(" ", "").strip()
    
    # Try exact match first
    if hsn_clean in HSN_GST_MAPPING:
        rate = HSN_GST_MAPPING[hsn_clean]
        return rate, f"HSN {hsn_clean}: {rate * 100}% GST (Local)", {
            'hsnCode': hsn_clean,
            'gstRate': rate * 100,
            'description': 'Local mapping',
            'source': 'Local Database'
        }
    
    # Try partial match for 4-digit HSN codes (match first 4 digits)
    if len(hsn_clean) >= 4:
        hsn_4digit = hsn_clean[:4]
        if hsn_4digit in HSN_GST_MAPPING:
            rate = HSN_GST_MAPPING[hsn_4digit]
            return rate, f"HSN {hsn_4digit}: {rate * 100}% GST (matched from {hsn_clean})", {
                'hsnCode': hsn_4digit,
                'gstRate': rate * 100,
                'description': f'Matched from {hsn_clean}',
                'source': 'Local Database'
            }
    
    # Try partial match for 2-digit HSN codes (match first 2 digits)
    if len(hsn_clean) >= 2:
        hsn_2digit = hsn_clean[:2]
        # Find similar HSN codes with same first 2 digits
        similar_hsns = [hsn for hsn in HSN_GST_MAPPING.keys() if hsn.startswith(hsn_2digit)]
        if similar_hsns:
            # Use the most common rate for this category
            rates = [HSN_GST_MAPPING[hsn] for hsn in similar_hsns]
            common_rate = max(set(rates), key=rates.count)  # Most frequent rate
            return common_rate, f"Category {hsn_2digit}: {common_rate * 100}% GST (estimated from {hsn_clean})", {
                'hsnCode': hsn_2digit,
                'gstRate': common_rate * 100,
                'description': f'Category estimate from {hsn_clean}',
                'source': 'Local Database'
            }
    
    # Default to 18% if no match found
    return 0.18, f"HSN {hsn_clean}: 18% GST (default rate - unknown HSN)", {
        'hsnCode': hsn_clean,
        'gstRate': 18,
        'description': 'Default rate - unknown HSN',
        'source': 'Default'
    }

def get_gst_rate_from_hsn(hsn_code):
    """
    Main function to get GST rate - tries API first, falls back to local mapping
    Returns tuple: (gst_rate, description)
    """
    gst_rate, description, hsn_data = get_gst_rate_from_hsn_api(hsn_code)
    return gst_rate, description

def configure_gemini(api_key):
    global genai_api_key
    genai_api_key = api_key
    genai.configure(api_key=api_key)

def scrape_product_data(url):
    """Scrape product dimensions and weight from e-commerce URLs"""
    try:
        print(f"Scraping URL: {url}")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        print(f"Response status: {response.status_code}, Content length: {len(response.content)}")
        
        soup = BeautifulSoup(response.content, 'html.parser')
        domain = urlparse(url).netloc.lower()
        print(f"Domain detected: {domain}")
        
        # Find all text containing "weight" or "dimension" for debugging
        page_text = soup.get_text()
        weight_mentions = [line.strip() for line in page_text.split('\n') if 'weight' in line.lower() and line.strip()]
        dimension_mentions = [line.strip() for line in page_text.split('\n') if any(word in line.lower() for word in ['dimension', 'size', 'length', 'width', 'height']) and line.strip() and len(line.strip()) < 200]
        
        print(f"Found {len(weight_mentions)} weight mentions")
        print(f"Found {len(dimension_mentions)} dimension mentions")
        if weight_mentions:
            print(f"Sample weight mentions: {weight_mentions[:3]}")
        if dimension_mentions:
            print(f"Sample dimension mentions: {dimension_mentions[:3]}")
        
        product_data = {
            'weight': None,
            'dimensions': {'length': None, 'width': None, 'height': None},
            'brand': None,
            'title': None
        }
        
        # Amazon scraping
        if 'amazon.' in domain:
            print("Using Amazon scraper")
            product_data = scrape_amazon(soup)
        # Flipkart scraping
        elif 'flipkart.' in domain:
            print("Using Flipkart scraper")
            product_data = scrape_flipkart(soup)
        # Meesho scraping
        elif 'meesho.' in domain:
            print("Using Meesho scraper")
            product_data = scrape_meesho(soup)
        # Generic scraping
        else:
            print("Using generic scraper")
            product_data = scrape_generic(soup)
        
        print(f"Final scraped data: {product_data}")
        return product_data
        
    except Exception as e:
        print(f"Error scraping product data: {e}")
        import traceback
        traceback.print_exc()
        return None

def scrape_amazon(soup):
    """Scrape Amazon product page"""
    data = {'weight': None, 'dimensions': {'length': None, 'width': None, 'height': None}, 'brand': None, 'title': None}
    
    # Enhanced title extraction with more selectors
    title_selectors = [
        {'id': 'productTitle'},
        {'class': 'a-size-large product-title-word-break'},
        {'class': 'a-size-large a-spacing-none a-color-base'},
        {'class': '_p13n-zg-list-grid-desktop'},
        'h1.a-size-large',
        'h1',
        'span#productTitle',
        '[data-automation-id="product-title"]'
    ]
    
    # Try multiple approaches for title
    for selector in title_selectors:
        try:
            if isinstance(selector, dict):
                title_elem = soup.find('span', selector) or soup.find('h1', selector) or soup.find('div', selector)
            else:
                title_elem = soup.select_one(selector) if '.' in selector or '#' in selector or '[' in selector else soup.find(selector)
            if title_elem and title_elem.get_text().strip():
                data['title'] = title_elem.get_text().strip()[:200]  # Limit title length
                break
        except:
            continue
    
    # Fallback: search for title in meta tags
    if not data['title']:
        meta_title = soup.find('meta', {'property': 'og:title'}) or soup.find('meta', {'name': 'title'})
        if meta_title and meta_title.get('content'):
            data['title'] = meta_title.get('content').strip()[:200]
    
    # Enhanced brand extraction
    brand_selectors = [
        {'class': 'a-spacing-small po-brand'},
        {'class': 'a-row a-spacing-small po-brand'},
        {'data-hook': 'brand-name'},
        {'class': 'brand'},
        {'class': 'a-text-bold'},
        'tr.a-spacing-small.po-brand td.a-span9 span.a-offscreen',
        '[data-testid="brand-name"]'
    ]
    
    for selector in brand_selectors:
        try:
            if isinstance(selector, dict):
                brand_elem = soup.find('tr', selector) or soup.find('span', selector) or soup.find('div', selector) or soup.find('td', selector)
            else:
                brand_elem = soup.select_one(selector)
            
            if brand_elem:
                # Try different ways to extract brand text
                brand_text = None
                brand_span = brand_elem.find('span', class_='a-offscreen')
                if brand_span:
                    brand_text = brand_span.get_text().strip()
                elif brand_elem.find('a'):
                    brand_text = brand_elem.find('a').get_text().strip()
                else:
                    brand_text = brand_elem.get_text().strip()
                
                if brand_text and len(brand_text) < 50:  # Reasonable brand name length
                    data['brand'] = brand_text
                    break
        except:
            continue
    
    # Fallback: look for brand in the page text using patterns
    if not data['brand']:
        page_text = soup.get_text()
        brand_patterns = [
            r'Brand[:\s]+([A-Za-z0-9\s&-]+?)(?:\n|Visit|Store|Shop)',
            r'by\s+([A-Za-z0-9\s&-]+?)(?:\n|\s{2,})',
            r'Manufacturer[:\s]+([A-Za-z0-9\s&-]+?)(?:\n|;)'
        ]
        
        for pattern in brand_patterns:
            brand_match = re.search(pattern, page_text, re.IGNORECASE)
            if brand_match:
                brand_candidate = brand_match.group(1).strip()
                if len(brand_candidate) < 50 and brand_candidate:
                    data['brand'] = brand_candidate
                    break
    
    # Comprehensive product details extraction
    detail_sections = [
        soup.find('table', {'id': 'productDetails_detailBullets_sections1'}),
        soup.find('div', {'id': 'productDetails_feature_div'}),
        soup.find('div', {'id': 'productDetails_techSpec_section_1'}),
        soup.find('div', {'class': 'a-section a-spacing-small'}),
        soup.find('ul', {'class': 'a-unordered-list a-nostyle a-vertical a-spacing-none detail-bullet-list'}),
        soup.find('div', {'id': 'detailBullets_feature_div'}),
        soup.find('table', {'class': 'a-keyvalue prodDetTable'}),
        soup.find('div', {'data-hook': 'product-details'})
    ]
    
    # Get page text for fallback extraction
    page_text = soup.get_text()
    page_text_lower = page_text.lower()
    
    print(f"Page text preview: {page_text[:500]}")  # Debug log
    
    # Enhanced weight extraction with more patterns
    weight_patterns = [
        r'item\s+weight[:\s]*(\d+(?:\.\d+)?)\s*(kg|kilograms?|grams?|g|pounds?|lbs?|oz)\b',
        r'product\s+weight[:\s]*(\d+(?:\.\d+)?)\s*(kg|kilograms?|grams?|g|pounds?|lbs?|oz)\b',
        r'shipping\s+weight[:\s]*(\d+(?:\.\d+)?)\s*(kg|kilograms?|grams?|g|pounds?|lbs?|oz)\b',
        r'weight[:\s]*(\d+(?:\.\d+)?)\s*(kg|kilograms?|grams?|g|pounds?|lbs?|oz)\b',
        r'(\d+(?:\.\d+)?)\s*(kg|kilograms?|grams?|g|pounds?|lbs?|oz)(?:\s+weight|\s+wt\.?)',
        r'weight[:\s]*(\d+(?:\.\d+)?)\s*([kKgG])\b',
        r'net\s+weight[:\s]*(\d+(?:\.\d+)?)\s*(kg|kilograms?|grams?|g|pounds?|lbs?|oz)\b'
    ]
    
    for pattern in weight_patterns:
        weight_match = re.search(pattern, page_text, re.IGNORECASE)
        if weight_match:
            try:
                weight_val = float(weight_match.group(1))
                unit = weight_match.group(2).lower()
                
                # Convert to kg
                if unit in ['g', 'gram', 'grams']:
                    weight_val = weight_val / 1000
                elif unit in ['pounds', 'lbs', 'lb']:
                    weight_val = weight_val * 0.453592
                elif unit in ['oz', 'ounce', 'ounces']:
                    weight_val = weight_val * 0.0283495
                elif unit in ['k', 'kg', 'kilogram', 'kilograms']:
                    pass  # Already in kg
                
                if 0.001 <= weight_val <= 1000:  # Reasonable weight range
                    data['weight'] = round(weight_val, 3)
                    print(f"Found weight: {data['weight']} kg")
                    break
            except ValueError:
                continue
    
    # Enhanced dimension extraction with more patterns
    dimension_patterns = [
        r'product\s+dimensions[:\s]*(\d+(?:\.\d+)?)\s*[×x]\s*(\d+(?:\.\d+)?)\s*[×x]\s*(\d+(?:\.\d+)?)',
        r'item\s+dimensions[:\s]*(\d+(?:\.\d+)?)\s*[×x]\s*(\d+(?:\.\d+)?)\s*[×x]\s*(\d+(?:\.\d+)?)',
        r'package\s+dimensions[:\s]*(\d+(?:\.\d+)?)\s*[×x]\s*(\d+(?:\.\d+)?)\s*[×x]\s*(\d+(?:\.\d+)?)',
        r'dimensions[:\s]*(\d+(?:\.\d+)?)\s*[×x]\s*(\d+(?:\.\d+)?)\s*[×x]\s*(\d+(?:\.\d+)?)',
        r'size[:\s]*(\d+(?:\.\d+)?)\s*[×x]\s*(\d+(?:\.\d+)?)\s*[×x]\s*(\d+(?:\.\d+)?)',
        r'(\d+(?:\.\d+)?)\s*[×x]\s*(\d+(?:\.\d+)?)\s*[×x]\s*(\d+(?:\.\d+)?)\s*(?:cm|centimeter|mm|millimeter|inch|inches|in)',
        r'(\d+(?:\.\d+)?)\s*cm\s*[×x]\s*(\d+(?:\.\d+)?)\s*cm\s*[×x]\s*(\d+(?:\.\d+)?)\s*cm'
    ]
    
    for pattern in dimension_patterns:
        dim_match = re.search(pattern, page_text, re.IGNORECASE)
        if dim_match:
            try:
                length = float(dim_match.group(1))
                width = float(dim_match.group(2))
                height = float(dim_match.group(3))
                
                # Reasonable dimension range (0.1cm to 500cm)
                if all(0.1 <= dim <= 500 for dim in [length, width, height]):
                    data['dimensions'] = {
                        'length': round(length, 2),
                        'width': round(width, 2),
                        'height': round(height, 2)
                    }
                    print(f"Found dimensions: {data['dimensions']}")
                    break
            except ValueError:
                continue
    
    # Try structured data extraction from detail sections
    for detail_section in detail_sections:
        if not detail_section:
            continue
            
        if detail_section.name == 'table':
            rows = detail_section.find_all('tr')
            for row in rows:
                label = row.find('th') or row.find('td', class_='a-span3')
                value = row.find('td') or row.find('td', class_='a-span9')
                if label and value:
                    label_text = label.get_text().strip().lower()
                    value_text = value.get_text().strip()
                    
                    if not data['weight'] and 'weight' in label_text:
                        weight_match = re.search(r'(\d+(?:\.\d+)?)\s*(kg|grams?|g|pounds?|lbs?)\b', value_text, re.IGNORECASE)
                        if weight_match:
                            weight_val = float(weight_match.group(1))
                            unit = weight_match.group(2).lower()
                            if unit in ['g', 'gram', 'grams']:
                                weight_val = weight_val / 1000
                            elif unit in ['pounds', 'lbs', 'lb']:
                                weight_val = weight_val * 0.453592
                            data['weight'] = round(weight_val, 2)
                    
                    if not any(data['dimensions'].values()) and 'dimension' in label_text:
                        dim_match = re.findall(r'(\d+(?:\.\d+)?)', value_text)
                        if len(dim_match) >= 3:
                            data['dimensions'] = {
                                'length': float(dim_match[0]),
                                'width': float(dim_match[1]),
                                'height': float(dim_match[2])
                            }
        
        elif detail_section.name in ['div', 'ul']:
            # Look for spans or list items containing weight/dimension info
            all_text = detail_section.get_text()
            if not data['weight'] and ('weight' in all_text.lower()):
                weight_match = re.search(r'weight[:\s]*(\d+(?:\.\d+)?)\s*(kg|grams?|g|pounds?|lbs?)\b', all_text, re.IGNORECASE)
                if weight_match:
                    weight_val = float(weight_match.group(1))
                    unit = weight_match.group(2).lower()
                    if unit in ['g', 'gram', 'grams']:
                        weight_val = weight_val / 1000
                    elif unit in ['pounds', 'lbs', 'lb']:
                        weight_val = weight_val * 0.453592
                    data['weight'] = round(weight_val, 2)
    
    return data

def scrape_flipkart(soup):
    """Scrape Flipkart product page"""
    data = {'weight': None, 'dimensions': {'length': None, 'width': None, 'height': None}, 'brand': None, 'title': None}
    
    # Title - multiple selectors
    title_selectors = [
        {'class': 'B_NuCI'},
        {'class': '_35KyD6'},
        {'class': 'yhB1nd'},
        'h1'
    ]
    
    for selector in title_selectors:
        if isinstance(selector, dict):
            title_elem = soup.find('span', selector) or soup.find('h1', selector)
        else:
            title_elem = soup.find(selector)
        if title_elem:
            data['title'] = title_elem.get_text().strip()
            break
    
    # Brand
    brand_elem = soup.find('a', class_='_2b3wE_') or soup.find('span', class_='_2b3wE_')
    if brand_elem:
        data['brand'] = brand_elem.get_text().strip()
    
    # Search in page text for weight and dimensions
    page_text = soup.get_text().lower()
    
    # Weight extraction
    weight_patterns = [
        r'item weight[:\s]*(\d+(?:\.\d+)?)\s*(kg|kilograms?|grams?|g)\b',
        r'product weight[:\s]*(\d+(?:\.\d+)?)\s*(kg|kilograms?|grams?|g)\b',
        r'weight[:\s]*(\d+(?:\.\d+)?)\s*(kg|kilograms?|grams?|g)\b'
    ]
    
    for pattern in weight_patterns:
        weight_match = re.search(pattern, page_text, re.IGNORECASE)
        if weight_match:
            weight_val = float(weight_match.group(1))
            unit = weight_match.group(2).lower()
            if unit in ['g', 'gram', 'grams']:
                weight_val = weight_val / 1000
            data['weight'] = round(weight_val, 2)
            break
    
    # Dimensions extraction
    dimension_patterns = [
        r'dimensions[:\s]*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)',
        r'size[:\s]*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)'
    ]
    
    for pattern in dimension_patterns:
        dim_match = re.search(pattern, page_text, re.IGNORECASE)
        if dim_match:
            data['dimensions'] = {
                'length': float(dim_match.group(1)),
                'width': float(dim_match.group(2)),
                'height': float(dim_match.group(3))
            }
            break
    
    # Specifications table
    spec_selectors = [
        {'class': '_14cfVK'},
        {'class': '_1UhVsV'},
        {'class': 'col col-3-12 _2H87wv'}
    ]
    
    for selector in spec_selectors:
        spec_tables = soup.find_all('table', selector)
        for table in spec_tables:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) >= 2:
                    label = cells[0].get_text().strip().lower()
                    value = cells[1].get_text().strip()
                    
                    if not data['weight'] and 'weight' in label:
                        weight_match = re.search(r'(\d+(?:\.\d+)?)\s*(kg|grams?|g)\b', value, re.IGNORECASE)
                        if weight_match:
                            weight_val = float(weight_match.group(1))
                            unit = weight_match.group(2).lower()
                            if unit in ['g', 'gram', 'grams']:
                                weight_val = weight_val / 1000
                            data['weight'] = round(weight_val, 2)
                    
                    if not any(data['dimensions'].values()) and 'dimension' in label:
                        dim_match = re.findall(r'(\d+(?:\.\d+)?)', value)
                        if len(dim_match) >= 3:
                            data['dimensions'] = {
                                'length': float(dim_match[0]),
                                'width': float(dim_match[1]),
                                'height': float(dim_match[2])
                            }
    
    return data

def scrape_meesho(soup):
    """Scrape Meesho product page"""
    data = {'weight': None, 'dimensions': {'length': None, 'width': None, 'height': None}, 'brand': None, 'title': None}
    
    # Basic implementation for Meesho
    title_elem = soup.find('h1')
    if title_elem:
        data['title'] = title_elem.get_text().strip()
    
    return data

def scrape_generic(soup):
    """Generic scraping for other sites"""
    data = {'weight': None, 'dimensions': {'length': None, 'width': None, 'height': None}, 'brand': None, 'title': None}
    
    # Try to find title
    title_elem = soup.find('h1') or soup.find('title')
    if title_elem:
        data['title'] = title_elem.get_text().strip()
    
    return data

def calculate_marketplace_shipping(weight, dimensions, marketplace='amazon'):
    """Calculate shipping charges for different marketplaces"""
    length = dimensions.get('length', 0)
    width = dimensions.get('width', 0) 
    height = dimensions.get('height', 0)
    
    # Calculate volumetric weight
    volumetric_weight = (length * width * height) / 5000 if all([length, width, height]) else 0
    
    # Use higher of actual weight or volumetric weight
    chargeable_weight = max(weight or 0, volumetric_weight)
    
    shipping_costs = {
        'amazon': calculate_amazon_shipping(chargeable_weight, dimensions),
        'flipkart': calculate_flipkart_shipping(chargeable_weight, dimensions),
        'meesho': calculate_meesho_shipping(chargeable_weight, dimensions)
    }
    
    if marketplace == 'all':
        return shipping_costs
    else:
        return shipping_costs.get(marketplace, shipping_costs['amazon'])

def calculate_amazon_shipping(weight, dimensions):
    """Amazon shipping calculation"""
    if weight <= 0.5:
        local_shipping = 45
        regional_shipping = 55
        national_shipping = 65
    elif weight <= 1:
        local_shipping = 60
        regional_shipping = 70
        national_shipping = 85
    elif weight <= 2:
        local_shipping = 80
        regional_shipping = 95
        national_shipping = 115
    else:
        # Per additional kg
        additional_kg = weight - 2
        local_shipping = 80 + (additional_kg * 25)
        regional_shipping = 95 + (additional_kg * 30)
        national_shipping = 115 + (additional_kg * 40)
    
    return {
        'local': round(local_shipping, 2),
        'regional': round(regional_shipping, 2),
        'national': round(national_shipping, 2),
        'average': round((local_shipping + regional_shipping + national_shipping) / 3, 2)
    }

def calculate_flipkart_shipping(weight, dimensions):
    """Flipkart shipping calculation"""
    if weight <= 0.5:
        shipping = 50
    elif weight <= 1:
        shipping = 75
    elif weight <= 2:
        shipping = 100
    else:
        additional_kg = weight - 2
        shipping = 100 + (additional_kg * 30)
    
    return {
        'local': round(shipping * 0.8, 2),
        'regional': round(shipping, 2),
        'national': round(shipping * 1.3, 2),
        'average': round(shipping, 2)
    }

def calculate_meesho_shipping(weight, dimensions):
    """Meesho shipping calculation"""
    if weight <= 0.5:
        shipping = 40
    elif weight <= 1:
        shipping = 60
    elif weight <= 2:
        shipping = 85
    else:
        additional_kg = weight - 2
        shipping = 85 + (additional_kg * 25)
    
    return {
        'local': round(shipping * 0.7, 2),
        'regional': round(shipping * 0.9, 2),
        'national': round(shipping * 1.2, 2),
        'average': round(shipping * 0.9, 2)
    }

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/generate-listing', methods=['POST'])
def generate_listing():
    try:
        print("Starting generate_listing...")
        data = request.get_json()
        print(f"Received data keys: {data.keys() if data else 'No data'}")
        
        if not genai_api_key:
            print("Error: Gemini API key not configured")
            return jsonify({'error': 'Gemini API key not configured'}), 400
        
        # Get image data
        image_data = data.get('image')
        if not image_data:
            print("Error: No image provided")
            return jsonify({'error': 'No image provided'}), 400
        
        print("Processing image data...")
        # Remove data URL prefix if present
        if 'base64,' in image_data:
            image_data = image_data.split('base64,')[1]
        
        # Decode base64 image
        try:
            image_bytes = base64.b64decode(image_data)
            image = Image.open(io.BytesIO(image_bytes))
            print(f"Image loaded successfully: {image.size}")
        except Exception as img_error:
            print(f"Image processing error: {img_error}")
            return jsonify({'error': f'Image processing failed: {str(img_error)}'}), 400
        
        # Get additional product info
        product_info = data.get('productInfo', {})
        product_name = product_info.get('name', '')
        brand = product_info.get('brand', '')
        dimensions = product_info.get('dimensions', '')
        cost_price = product_info.get('costPrice', 0)
        print(f"Product info: name={product_name}, brand={brand}")
        
        # Create prompt for Gemini
        prompt = f"""
        Analyze this product image and generate 3 different e-commerce listing versions for Indian marketplaces (Amazon, Flipkart, Meesho).
        Each version should have different copywriting styles and target different customer segments.
        
        Additional product information:
        - Product Name: {product_name}
        - Brand: {brand}
        - Dimensions: {dimensions}
        - Cost Price: ₹{cost_price}
        
        For HSN codes, please provide accurate 4-6 digit HSN codes based on the actual product category. Common examples:
        - Electronics/Mobile phones: 8517 (18% GST)
        - Toys and games: 9503 (12% GST for most toys, 18% for electronic toys)
        - Clothing/Textiles: 6101-6302 (5-12% GST depending on material and type)
        - Books/Printed material: 4901-4911 (0-12% GST)
        - Furniture: 9403 (12% GST)
        - Footwear: 6403-6405 (5-18% GST depending on material)
        - Plastic items: 3926 (18% GST)
        - Kitchen items/utensils: 7323 (18% GST)
        - Cosmetics: 3303-3307 (18% GST)
        - Sports goods: 9506 (18% GST)
        
        Please analyze the product image carefully and assign the most appropriate HSN code based on the actual product category and material.
        
        Please provide a JSON response with the following structure:
        {{
            "amazon": [
                {{
                    "version": 1,
                    "style": "Professional & Feature-focused",
                    "title": "Product title under 200 characters",
                    "bulletPoints": ["3-5 bullet points under 250 chars each"],
                    "description": "50-75 words description",
                    "category": "Suggested category",
                    "hsnCode": "Accurate 4-digit HSN code for this product category",
                    "keywords": ["comma-separated SEO keywords"]
                }},
                {{
                    "version": 2,
                    "style": "Value & Benefits-focused",
                    "title": "Different product title under 200 characters",
                    "bulletPoints": ["3-5 different bullet points under 250 chars each"],
                    "description": "Different 50-75 words description",
                    "category": "Suggested category",
                    "hsnCode": "HSN code preferably from 5% GST slab",
                    "keywords": ["different comma-separated SEO keywords"]
                }},
                {{
                    "version": 3,
                    "style": "Emotional & Lifestyle-focused",
                    "title": "Third product title under 200 characters",
                    "bulletPoints": ["3-5 more bullet points under 250 chars each"],
                    "description": "Third 50-75 words description",
                    "category": "Suggested category",
                    "hsnCode": "HSN code preferably from 5% GST slab",
                    "keywords": ["third set of comma-separated SEO keywords"]
                }}
            ],
            "flipkart": [
                {{
                    "version": 1,
                    "style": "Specification-heavy",
                    "title": "Flipkart-optimized title under 200 characters",
                    "bulletPoints": ["3-5 spec-focused bullet points under 250 chars each"],
                    "description": "Technical 50-75 words description",
                    "category": "Suggested category",
                    "hsnCode": "HSN code preferably from 5% GST slab",
                    "keywords": ["technical SEO keywords"]
                }},
                {{
                    "version": 2,
                    "style": "Comparison & USP-focused",
                    "title": "Comparison-based title under 200 characters",
                    "bulletPoints": ["3-5 comparison bullet points under 250 chars each"],
                    "description": "USP-focused 50-75 words description",
                    "category": "Suggested category",
                    "hsnCode": "HSN code preferably from 5% GST slab",
                    "keywords": ["comparison SEO keywords"]
                }},
                {{
                    "version": 3,
                    "style": "Trendy & Modern",
                    "title": "Trendy title under 200 characters",
                    "bulletPoints": ["3-5 modern lifestyle bullet points under 250 chars each"],
                    "description": "Modern 50-75 words description",
                    "category": "Suggested category",
                    "hsnCode": "HSN code preferably from 5% GST slab",
                    "keywords": ["trendy SEO keywords"]
                }}
            ],
            "meesho": [
                {{
                    "version": 1,
                    "style": "Budget-conscious",
                    "title": "Value-focused title under 200 characters",
                    "bulletPoints": ["3-5 value-focused bullet points under 250 chars each"],
                    "description": "Budget-friendly 50-75 words description",
                    "category": "Suggested category",
                    "hsnCode": "HSN code preferably from 5% GST slab",
                    "keywords": ["budget SEO keywords"]
                }},
                {{
                    "version": 2,
                    "style": "Family-oriented",
                    "title": "Family-friendly title under 200 characters",
                    "bulletPoints": ["3-5 family-focused bullet points under 250 chars each"],
                    "description": "Family-oriented 50-75 words description",
                    "category": "Suggested category",
                    "hsnCode": "HSN code preferably from 5% GST slab",
                    "keywords": ["family SEO keywords"]
                }},
                {{
                    "version": 3,
                    "style": "Local & Regional",
                    "title": "Regional appeal title under 200 characters",
                    "bulletPoints": ["3-5 regional bullet points under 250 chars each"],
                    "description": "Regional 50-75 words description",
                    "category": "Suggested category",
                    "hsnCode": "HSN code preferably from 5% GST slab",
                    "keywords": ["regional SEO keywords"]
                }}
            ]
        }}
        
        Make each version unique with different copywriting approaches, target different customer personas, and use varied language styles suitable for Indian customers.
        """
        
        # Generate content with Gemini Vision
        try:
            print("Calling Gemini API...")
            model = genai.GenerativeModel('gemini-1.5-flash')
            response = model.generate_content([prompt, image])
            print("Gemini API call successful")
        except Exception as api_error:
            print(f"Gemini API error: {api_error}")
            return jsonify({'error': f'Gemini API call failed: {str(api_error)}'}), 500
        
        # Parse the response
        try:
            print("Parsing Gemini response...")
            # Extract JSON from response
            response_text = response.text
            print(f"Raw response: {response_text[:200]}...")
            
            if '```json' in response_text:
                json_start = response_text.find('```json') + 7
                json_end = response_text.find('```', json_start)
                response_text = response_text[json_start:json_end]
            elif '{' in response_text and '}' in response_text:
                json_start = response_text.find('{')
                json_end = response_text.rfind('}') + 1
                response_text = response_text[json_start:json_end]
            
            listing_data = json.loads(response_text)
            print("JSON parsing successful")
        except Exception as parse_error:
            print(f"JSON parsing error: {parse_error}")
            # Fallback if JSON parsing fails
            listing_data = {
                "amazon": [
                    {
                        "version": 1,
                        "style": "Professional",
                        "title": f"{brand} {product_name} - Premium Quality".strip() or "Premium Quality Product",
                        "bulletPoints": ["High quality product", "Suitable for daily use", "Durable and long-lasting"],
                        "description": "Quality product with excellent features and reliable performance for everyday use.",
                        "category": "General",
                        "hsnCode": "9999",
                        "keywords": ["quality", "durable", "reliable"]
                    },
                    {
                        "version": 2,
                        "style": "Value-focused",
                        "title": f"{brand} {product_name} - Best Value".strip() or "Best Value Product",
                        "bulletPoints": ["Excellent value for money", "Cost-effective solution", "Great performance"],
                        "description": "Get the best value with this cost-effective product that delivers great performance.",
                        "category": "General",
                        "hsnCode": "9999",
                        "keywords": ["value", "affordable", "performance"]
                    },
                    {
                        "version": 3,
                        "style": "Lifestyle",
                        "title": f"{brand} {product_name} - Lifestyle Choice".strip() or "Lifestyle Product",
                        "bulletPoints": ["Perfect for modern lifestyle", "Stylish and functional", "Enhances daily routine"],
                        "description": "Upgrade your lifestyle with this stylish and functional product for modern living.",
                        "category": "General",
                        "hsnCode": "9999",
                        "keywords": ["lifestyle", "modern", "stylish"]
                    }
                ],
                "flipkart": [
                    {
                        "version": 1,
                        "style": "Technical",
                        "title": f"{brand} {product_name} - Advanced Features".strip() or "Advanced Feature Product",
                        "bulletPoints": ["Advanced technology", "Superior specifications", "Technical excellence"],
                        "description": "Experience advanced technology with superior specifications and technical excellence.",
                        "category": "General",
                        "hsnCode": "9999",
                        "keywords": ["advanced", "technology", "specifications"]
                    },
                    {
                        "version": 2,
                        "style": "Comparison",
                        "title": f"{brand} {product_name} - Superior Choice".strip() or "Superior Choice Product",
                        "bulletPoints": ["Better than competitors", "Proven superiority", "Top-rated choice"],
                        "description": "Choose the superior option that outperforms competitors with proven quality.",
                        "category": "General",
                        "hsnCode": "9999",
                        "keywords": ["superior", "better", "top-rated"]
                    },
                    {
                        "version": 3,
                        "style": "Trendy",
                        "title": f"{brand} {product_name} - Trending Now".strip() or "Trending Product",
                        "bulletPoints": ["Latest trend", "Popular choice", "Modern design"],
                        "description": "Stay on-trend with this popular choice featuring modern design and latest features.",
                        "category": "General",
                        "hsnCode": "9999",
                        "keywords": ["trending", "popular", "modern"]
                    }
                ],
                "meesho": [
                    {
                        "version": 1,
                        "style": "Budget",
                        "title": f"{brand} {product_name} - Affordable Quality".strip() or "Affordable Quality Product",
                        "bulletPoints": ["Budget-friendly price", "Great savings", "Affordable excellence"],
                        "description": "Get quality at an affordable price with great savings and excellent value.",
                        "category": "General",
                        "hsnCode": "9999",
                        "keywords": ["affordable", "budget", "savings"]
                    },
                    {
                        "version": 2,
                        "style": "Family",
                        "title": f"{brand} {product_name} - Family Choice".strip() or "Family Choice Product",
                        "bulletPoints": ["Perfect for families", "Safe and reliable", "Family-friendly design"],
                        "description": "The perfect family choice with safe, reliable design for all family members.",
                        "category": "General",
                        "hsnCode": "9999",
                        "keywords": ["family", "safe", "reliable"]
                    },
                    {
                        "version": 3,
                        "style": "Regional",
                        "title": f"{brand} {product_name} - Local Favorite".strip() or "Local Favorite Product",
                        "bulletPoints": ["Locally popular", "Regional favorite", "Community choice"],
                        "description": "Join the community choice with this locally popular and regionally favored product.",
                        "category": "General",
                        "hsnCode": "9999",
                        "keywords": ["local", "community", "popular"]
                    }
                ]
            }
            print("Using fallback listing data")
        
        print("Returning successful response")
        return jsonify({'success': True, 'data': listing_data})
        
    except Exception as e:
        print(f"Unexpected error in generate_listing: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/calculate-price', methods=['POST'])
def calculate_price():
    try:
        data = request.get_json()
        
        # Helper function to safely convert to float
        def safe_float(value, default=0):
            if value is None or value == '' or value == 'null':
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default
        
        cost_price = safe_float(data.get('costPrice', 0))
        profit_margin = safe_float(data.get('profitMargin', 42.5)) / 100
        hsn_code = data.get('hsnCode', '9999')  # Get HSN code from request
        marketplace = data.get('marketplace', 'amazon')
        
        # Get GST rate based on HSN code
        gst_rate, gst_description = get_gst_rate_from_hsn(hsn_code)
        
        # Get weight and dimensions
        weight = safe_float(data.get('weight', 0))
        length = safe_float(data.get('length', 0))
        width = safe_float(data.get('width', 0))
        height = safe_float(data.get('height', 0))
        
        dimensions = {'length': length, 'width': width, 'height': height}
        
        # Calculate marketplace-specific shipping
        shipping_data = calculate_marketplace_shipping(weight, dimensions, 'all')
        
        # Platform commission rates
        commission_rates = {
            'amazon': 0.15,  # 15%
            'flipkart': 0.12,  # 12%
            'meesho': 0.08   # 8%
        }
        
        price_breakdowns = {}
        
        for platform, shipping_info in shipping_data.items():
            platform_commission = commission_rates.get(platform, 0.15)
            avg_shipping = shipping_info['average']
            
            # Price calculation
            cost_with_gst = cost_price * (1 + gst_rate)
            target_profit = cost_price * profit_margin
            
            # Calculate selling price considering all costs
            base_price = cost_with_gst + target_profit + avg_shipping
            final_price = base_price / (1 - platform_commission)
            
            mrp = final_price * 1.2  # 20% above selling price for MRP
            
            price_breakdowns[platform] = {
                'costPrice': cost_price,
                'gst': round(cost_price * gst_rate, 2),
                'gstRate': f"{gst_rate * 100}%",
                'gstDescription': gst_description,
                'hsnCode': hsn_code,
                'targetProfit': round(target_profit, 2),
                'shippingCost': round(avg_shipping, 2),
                'shippingDetails': shipping_info,
                'platformCommission': round(final_price * platform_commission, 2),
                'platformCommissionRate': f"{platform_commission * 100}%",
                'sellingPrice': round(final_price, 2),
                'mrp': round(mrp, 2),
                'weight': weight,
                'volumetricWeight': round((length * width * height) / 5000, 2) if all([length, width, height]) else 0
            }
        
        return jsonify({'success': True, 'data': price_breakdowns})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/<format>', methods=['POST'])
def export_listing(format):
    try:
        data = request.get_json()
        listing_versions = data.get('listing', [])
        pricing = data.get('pricing', {})
        
        print(f"Export format: {format}")
        print(f"Listing versions: {len(listing_versions) if isinstance(listing_versions, list) else 'single listing'}")
        print(f"Pricing data: {pricing}")
        
        # Handle both single listing (manual mode) and multiple versions (AI mode)
        if not isinstance(listing_versions, list):
            listing_versions = [listing_versions]
        
        if not listing_versions or not listing_versions[0]:
            return jsonify({'error': 'No listing data provided'}), 400
        
        # Create temporary file with proper extension
        file_extension = 'xlsx' if format in ['amazon', 'meesho'] else 'csv'
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{file_extension}')
        temp_file.close()  # Close the file so pandas can write to it
        
        try:
            if format == 'amazon':
                # Amazon Flat File format with multiple versions
                amazon_data = {
                    'Version': [],
                    'Style': [],
                    'Product Title': [],
                    'Product Description': [],
                    'Bullet Point 1': [],
                    'Bullet Point 2': [],
                    'Bullet Point 3': [],
                    'Bullet Point 4': [],
                    'Bullet Point 5': [],
                    'Standard Price': [],
                    'Sale Price': [],
                    'Keywords': [],
                    'HSN Code': []
                }
                
                for version in listing_versions:
                    amazon_data['Version'].append(version.get('version', 1))
                    amazon_data['Style'].append(version.get('style', 'Standard'))
                    amazon_data['Product Title'].append(version.get('title', ''))
                    amazon_data['Product Description'].append(version.get('description', ''))
                    bullets = version.get('bulletPoints', [])
                    for i in range(5):
                        amazon_data[f'Bullet Point {i+1}'].append(bullets[i] if i < len(bullets) else '')
                    
                    # Handle missing pricing data gracefully
                    if pricing and pricing.get('amazon'):
                        amazon_data['Standard Price'].append(pricing.get('amazon', {}).get('mrp', 0))
                        amazon_data['Sale Price'].append(pricing.get('amazon', {}).get('sellingPrice', 0))
                    else:
                        amazon_data['Standard Price'].append(0)
                        amazon_data['Sale Price'].append(0)
                    
                    keywords = version.get('keywords', [])
                    amazon_data['Keywords'].append(', '.join(keywords) if isinstance(keywords, list) else str(keywords))
                    amazon_data['HSN Code'].append(version.get('hsnCode', ''))
                
                df = pd.DataFrame(amazon_data)
                with pd.ExcelWriter(temp_file.name, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False, sheet_name='Amazon Listings')
                
            elif format == 'flipkart':
                # Flipkart CSV format with multiple versions
                flipkart_data = {
                    'Version': [],
                    'Style': [],
                    'Product Name': [],
                    'Product Description': [],
                    'Key Features': [],
                    'MRP': [],
                    'Selling Price': [],
                    'Category': [],
                    'HSN': [],
                    'Keywords': []
                }
                
                for version in listing_versions:
                    flipkart_data['Version'].append(version.get('version', 1))
                    flipkart_data['Style'].append(version.get('style', 'Standard'))
                    flipkart_data['Product Name'].append(version.get('title', ''))
                    flipkart_data['Product Description'].append(version.get('description', ''))
                    flipkart_data['Key Features'].append('; '.join(version.get('bulletPoints', [])))
                    
                    # Handle missing pricing data gracefully
                    if pricing and pricing.get('flipkart'):
                        flipkart_data['MRP'].append(pricing.get('flipkart', {}).get('mrp', 0))
                        flipkart_data['Selling Price'].append(pricing.get('flipkart', {}).get('sellingPrice', 0))
                    else:
                        flipkart_data['MRP'].append(0)
                        flipkart_data['Selling Price'].append(0)
                    
                    flipkart_data['Category'].append(version.get('category', ''))
                    flipkart_data['HSN'].append(version.get('hsnCode', ''))
                    keywords = version.get('keywords', [])
                    flipkart_data['Keywords'].append(', '.join(keywords) if isinstance(keywords, list) else str(keywords))
                
                df = pd.DataFrame(flipkart_data)
                df.to_csv(temp_file.name, index=False, encoding='utf-8')
                
            elif format == 'meesho':
                # Meesho Excel format with multiple versions
                meesho_data = {
                    'Version': [],
                    'Style': [],
                    'Product Title': [],
                    'Product Description': [],
                    'Features': [],
                    'MRP': [],
                    'Supplier Price': [],
                    'Category': [],
                    'HSN Code': [],
                    'Tags': []
                }
                
                for version in listing_versions:
                    meesho_data['Version'].append(version.get('version', 1))
                    meesho_data['Style'].append(version.get('style', 'Standard'))
                    meesho_data['Product Title'].append(version.get('title', ''))
                    meesho_data['Product Description'].append(version.get('description', ''))
                    meesho_data['Features'].append('\n'.join(version.get('bulletPoints', [])))
                    
                    # Handle missing pricing data gracefully
                    if pricing and pricing.get('meesho'):
                        meesho_data['MRP'].append(pricing.get('meesho', {}).get('mrp', 0))
                        meesho_data['Supplier Price'].append(pricing.get('meesho', {}).get('sellingPrice', 0))
                    else:
                        meesho_data['MRP'].append(0)
                        meesho_data['Supplier Price'].append(0)
                    
                    meesho_data['Category'].append(version.get('category', ''))
                    meesho_data['HSN Code'].append(version.get('hsnCode', ''))
                    keywords = version.get('keywords', [])
                    meesho_data['Tags'].append(', '.join(keywords) if isinstance(keywords, list) else str(keywords))
                
                df = pd.DataFrame(meesho_data)
                with pd.ExcelWriter(temp_file.name, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False, sheet_name='Meesho Listings')
            
            print(f"File created successfully: {temp_file.name}")
            
            # Verify file exists and has content
            if not os.path.exists(temp_file.name):
                return jsonify({'error': 'Failed to create export file'}), 500
            
            file_size = os.path.getsize(temp_file.name)
            if file_size == 0:
                return jsonify({'error': 'Export file is empty'}), 500
            
            print(f"File size: {file_size} bytes")
            
            return send_file(temp_file.name, as_attachment=True, 
                            download_name=f'product_listing_{format}_versions_{datetime.now().strftime("%Y%m%d_%H%M%S")}.{file_extension}',
                            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if file_extension == 'xlsx' else 'text/csv')
        
        except Exception as export_error:
            print(f"Export error: {export_error}")
            # Clean up temp file on error
            if os.path.exists(temp_file.name):
                os.unlink(temp_file.name)
            raise export_error
        
    except Exception as e:
        print(f"General export error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Export failed: {str(e)}'}), 500

@app.route('/api/validate-hsn', methods=['POST'])
def validate_hsn():
    try:
        data = request.get_json()
        hsn_code = data.get('hsnCode', '')
        
        if not hsn_code:
            return jsonify({'error': 'HSN code is required'}), 400
        
        gst_rate, description, hsn_data = get_gst_rate_from_hsn_api(hsn_code)
        
        response_data = {
            'success': True,
            'hsnCode': hsn_code,
            'gstRate': gst_rate * 100,
            'gstRateDecimal': gst_rate,
            'description': description,
            'isKnownHsn': hsn_code in HSN_GST_MAPPING
        }
        
        # Add additional data if available from API
        if hsn_data:
            response_data.update({
                'apiHsnCode': hsn_data.get('hsnCode'),
                'chapterName': hsn_data.get('chapterName'),
                'productDescription': hsn_data.get('description'),
                'source': hsn_data.get('source')
            })
        
        return jsonify(response_data)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/configure-gemini', methods=['POST'])
def configure_gemini_api():
    try:
        data = request.get_json()
        api_key = data.get('apiKey')
        
        if not api_key:
            return jsonify({'error': 'API key is required'}), 400
        
        configure_gemini(api_key)
        return jsonify({'success': True, 'message': 'Gemini API configured successfully'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/scrape-product', methods=['POST'])
def scrape_product():
    try:
        data = request.get_json()
        url = data.get('url')
        
        if not url:
            return jsonify({'error': 'URL is required'}), 400
        
        product_data = scrape_product_data(url)
        
        if product_data:
            # Calculate shipping for all marketplaces
            if product_data['weight'] or any(product_data['dimensions'].values()):
                shipping_costs = calculate_marketplace_shipping(
                    product_data['weight'], 
                    product_data['dimensions'], 
                    'all'
                )
                product_data['shipping'] = shipping_costs
            
            return jsonify({'success': True, 'data': product_data})
        else:
            return jsonify({'error': 'Could not extract product data from URL'}), 400
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Advanced feature functions
def generate_product_image_variations(product_title, brand, category):
    """Generate lifestyle image prompts for AI image generation"""
    lifestyle_scenarios = [
        f"{product_title} being used in a modern living room setting",
        f"Person enjoying {product_title} in a cozy home environment",
        f"{product_title} displayed elegantly on a marble surface with natural lighting",
        f"Lifestyle shot of {product_title} in use during daily routine",
        f"{product_title} artistically arranged with complementary lifestyle items",
        f"Professional product photography of {product_title} with premium styling"
    ]
    
    return {
        'prompts': lifestyle_scenarios,
        'suggested_dimensions': {
            'amazon': {'width': 2000, 'height': 2000},
            'flipkart': {'width': 1200, 'height': 1200},
            'meesho': {'width': 800, 'height': 800}
        },
        'enhancement_tips': [
            'Use natural lighting for better appeal',
            'Include lifestyle elements to show product in use',
            'Maintain consistent brand colors',
            'Ensure high contrast for marketplace visibility'
        ]
    }

def calculate_comprehensive_gst(cost_price, hsn_code, state_from="Delhi", state_to="Mumbai"):
    """Calculate comprehensive GST including CGST, SGST, IGST"""
    gst_rate, description, hsn_data = get_gst_rate_from_hsn_api(hsn_code)
    
    # Calculate different GST components
    gst_amount = cost_price * gst_rate
    
    # Inter-state vs Intra-state GST calculation
    if state_from == state_to:
        # Intra-state: CGST + SGST
        cgst = gst_amount / 2
        sgst = gst_amount / 2
        igst = 0
    else:
        # Inter-state: IGST
        cgst = 0
        sgst = 0
        igst = gst_amount
    
    # TCS (Tax Collected at Source) for high-value items
    tcs_threshold = 50000
    tcs_rate = 0.001 if cost_price > tcs_threshold else 0
    tcs_amount = cost_price * tcs_rate
    
    # TDS (Tax Deducted at Source) for business purchases
    tds_rate = 0.001 if cost_price > 5000 else 0
    tds_amount = cost_price * tds_rate
    
    return {
        'costPrice': cost_price,
        'hsnCode': hsn_code,
        'gstRate': gst_rate * 100,
        'gstAmount': round(gst_amount, 2),
        'cgst': round(cgst, 2),
        'sgst': round(sgst, 2),
        'igst': round(igst, 2),
        'tcs': round(tcs_amount, 2),
        'tds': round(tds_amount, 2),
        'totalTax': round(gst_amount + tcs_amount, 2),
        'priceWithTax': round(cost_price + gst_amount + tcs_amount, 2),
        'gstDescription': description,
        'taxType': 'Intra-state' if state_from == state_to else 'Inter-state'
    }

def optimize_image_for_platforms(image_dimensions, platforms=['amazon', 'flipkart', 'meesho']):
    """Generate optimized image specifications for different platforms"""
    platform_specs = {
        'amazon': {
            'main_image': {'width': 2000, 'height': 2000, 'format': 'JPEG', 'quality': 85},
            'additional_images': {'width': 1600, 'height': 1600, 'format': 'JPEG', 'quality': 80},
            'zoom_requirement': True,
            'background': 'Pure white (RGB 255,255,255)',
            'file_size_limit': '10MB'
        },
        'flipkart': {
            'main_image': {'width': 1200, 'height': 1200, 'format': 'JPEG', 'quality': 80},
            'additional_images': {'width': 800, 'height': 800, 'format': 'JPEG', 'quality': 75},
            'zoom_requirement': False,
            'background': 'White or transparent',
            'file_size_limit': '5MB'
        },
        'meesho': {
            'main_image': {'width': 800, 'height': 800, 'format': 'JPEG', 'quality': 75},
            'additional_images': {'width': 600, 'height': 600, 'format': 'JPEG', 'quality': 70},
            'zoom_requirement': False,
            'background': 'Any solid color',
            'file_size_limit': '2MB'
        }
    }
    
    optimization_tips = {
        'cropping': 'Ensure product occupies 85% of image area',
        'lighting': 'Use soft, even lighting to avoid harsh shadows',
        'angles': 'Include front, side, and detail shots',
        'text_overlay': 'Avoid text on main image for Amazon',
        'watermarks': 'Remove all watermarks and logos'
    }
    
    return {
        'platform_specs': {platform: platform_specs[platform] for platform in platforms},
        'optimization_tips': optimization_tips
    }

def create_ab_test_variations(original_title, original_description, original_bullets):
    """Generate A/B testing variations for titles and descriptions"""
    
    # Title variations
    title_variations = [
        f"Premium {original_title}",
        f"Best {original_title}",
        f"{original_title} - Top Quality",
        f"Professional {original_title}",
        f"{original_title} - Limited Edition"
    ]
    
    # Description emotion variations
    emotion_prefixes = [
        "Experience the luxury of",
        "Discover the convenience of",
        "Enjoy the reliability of",
        "Transform your life with",
        "Upgrade your lifestyle with"
    ]
    
    description_variations = []
    for prefix in emotion_prefixes:
        description_variations.append(f"{prefix} {original_description.lower()}")
    
    # Bullet point variations (power words)
    power_words = ["Premium", "Professional", "Advanced", "Innovative", "Exclusive", "Superior"]
    bullet_variations = []
    
    for bullet in original_bullets:
        if bullet:
            variations = []
            for word in power_words:
                if word.lower() not in bullet.lower():
                    variations.append(f"{word} {bullet}")
            bullet_variations.append(variations[:3])  # Top 3 variations
    
    return {
        'title_variations': title_variations,
        'description_variations': description_variations,
        'bullet_variations': bullet_variations,
        'test_metrics': [
            'Click-through rate (CTR)',
            'Conversion rate',
            'Add to cart rate',
            'Bounce rate',
            'Time on page'
        ],
        'test_duration_recommendation': '2-4 weeks minimum'
    }

def analyze_review_sentiment(competitor_reviews):
    """Analyze sentiment of competitor reviews to find improvement opportunities"""
    positive_keywords = ['good', 'great', 'excellent', 'amazing', 'love', 'perfect', 'quality', 'fast', 'recommend']
    negative_keywords = ['bad', 'terrible', 'awful', 'hate', 'slow', 'poor', 'broken', 'defective', 'waste']
    
    if competitor_reviews and len(competitor_reviews) > 0:
        # Simple keyword-based sentiment analysis
        total_sentiment_score = 0
        positive_count = 0
        negative_count = 0
        
        for review in competitor_reviews:
            review_text = str(review).lower()
            positive_score = sum(1 for word in positive_keywords if word in review_text)
            negative_score = sum(1 for word in negative_keywords if word in review_text)
            
            if positive_score > negative_score:
                positive_count += 1
                total_sentiment_score += 0.8
            elif negative_score > positive_score:
                negative_count += 1
                total_sentiment_score += 0.2
            else:
                total_sentiment_score += 0.5
        
        sentiment_score = total_sentiment_score / len(competitor_reviews) if competitor_reviews else 0.5
        positive_percentage = round((positive_count / len(competitor_reviews)) * 100) if competitor_reviews else 50
        negative_percentage = round((negative_count / len(competitor_reviews)) * 100) if competitor_reviews else 30
    else:
        # Default simulated values when no reviews provided
        sentiment_score = 0.72
        positive_percentage = 68
        negative_percentage = 32
    
    simulated_analysis = {
        'sentiment_score': sentiment_score,
        'total_reviews_analyzed': len(competitor_reviews) if competitor_reviews else 100,
        'positive_percentage': positive_percentage,
        'negative_percentage': negative_percentage,
        'common_complaints': [
            'Packaging could be better',
            'Delivery was delayed',
            'Size runs small',
            'Instructions unclear',
            'Customer service slow'
        ],
        'positive_highlights': [
            'Great value for money',
            'Good build quality',
            'Fast shipping',
            'Easy to use',
            'Looks exactly like photos'
        ],
        'improvement_opportunities': [
            'Improve packaging quality',
            'Add detailed size guide',
            'Include clearer instructions',
            'Enhance customer service response time',
            'Offer better return policy'
        ]
    }
    
    return simulated_analysis

def track_keyword_rankings(keywords, platforms=['amazon', 'flipkart', 'meesho']):
    """Simulate keyword ranking tracking across platforms"""
    
    ranking_data = {}
    
    for platform in platforms:
        platform_rankings = {}
        for keyword in keywords:
            if keyword:
                # Simulate ranking data
                platform_rankings[keyword] = {
                    'current_rank': random.randint(1, 100),
                    'previous_rank': random.randint(1, 100),
                    'search_volume': random.randint(100, 5000),
                    'competition_level': random.choice(['Low', 'Medium', 'High']),
                    'trending': random.choice([True, False]),
                    'suggested_bid': round(random.uniform(0.5, 5.0), 2)
                }
        
        ranking_data[platform] = platform_rankings
    
    return {
        'rankings': ranking_data,
        'recommendations': [
            'Focus on long-tail keywords for better ranking',
            'Optimize product images for better visibility',
            'Improve product reviews and ratings',
            'Use relevant keywords in title and description',
            'Monitor competitor keyword strategies'
        ],
        'last_updated': datetime.now().isoformat()
    }

def analyze_market_trends(category, timeframe='30d'):
    """Analyze market trends and popular keywords"""
    
    # Simulated trend data
    trending_keywords = [
        {'keyword': 'eco-friendly', 'growth': '+25%', 'volume': 15000},
        {'keyword': 'sustainable', 'growth': '+18%', 'volume': 12000},
        {'keyword': 'premium quality', 'growth': '+12%', 'volume': 8000},
        {'keyword': 'fast delivery', 'growth': '+8%', 'volume': 20000},
        {'keyword': 'budget-friendly', 'growth': '+15%', 'volume': 18000}
    ]
    
    seasonal_trends = {
        'Q1': ['New Year', 'Valentine', 'Health & Fitness'],
        'Q2': ['Summer', 'Mother\'s Day', 'Outdoor'],
        'Q3': ['Back to School', 'Monsoon', 'Festive Prep'],
        'Q4': ['Festival Season', 'Winter', 'Gift Items']
    }
    
    return {
        'trending_keywords': trending_keywords,
        'seasonal_trends': seasonal_trends,
        'category_insights': {
            'fastest_growing_subcategories': [
                'Smart Home Devices',
                'Sustainable Products',
                'Health & Wellness',
                'Work from Home Essentials'
            ],
            'emerging_trends': [
                'Voice-activated products',
                'Biodegradable packaging',
                'Contactless features',
                'Multi-functional items'
            ]
        },
        'price_trends': {
            'average_price_increase': '5-8% annually',
            'premium_segment_growth': '12% YoY',
            'budget_segment_growth': '15% YoY'
        },
        'analysis_date': datetime.now().isoformat()
    }

# New API endpoints for advanced features

@app.route('/api/generate-product-images', methods=['POST'])
def generate_product_images():
    try:
        data = request.get_json()
        product_title = data.get('title', '')
        brand = data.get('brand', '')
        category = data.get('category', '')
        
        image_data = generate_product_image_variations(product_title, brand, category)
        return jsonify({'success': True, 'data': image_data})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/calculate-comprehensive-gst', methods=['POST'])
def calculate_comprehensive_gst_api():
    try:
        data = request.get_json()
        cost_price = float(data.get('costPrice', 0))
        hsn_code = data.get('hsnCode', '9999')
        state_from = data.get('stateFrom', 'Delhi')
        state_to = data.get('stateTo', 'Mumbai')
        
        gst_data = calculate_comprehensive_gst(cost_price, hsn_code, state_from, state_to)
        return jsonify({'success': True, 'data': gst_data})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/optimize-images', methods=['POST'])
def optimize_images():
    try:
        data = request.get_json()
        image_dimensions = data.get('dimensions', {})
        platforms = data.get('platforms', ['amazon', 'flipkart', 'meesho'])
        
        optimization_data = optimize_image_for_platforms(image_dimensions, platforms)
        return jsonify({'success': True, 'data': optimization_data})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/create-ab-test', methods=['POST'])
def create_ab_test():
    try:
        data = request.get_json()
        title = data.get('title', '')
        description = data.get('description', '')
        bullets = data.get('bulletPoints', [])
        
        ab_test_data = create_ab_test_variations(title, description, bullets)
        return jsonify({'success': True, 'data': ab_test_data})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/analyze-reviews', methods=['POST'])
def analyze_reviews():
    try:
        data = request.get_json()
        reviews = data.get('reviews', [])
        
        sentiment_data = analyze_review_sentiment(reviews)
        return jsonify({'success': True, 'data': sentiment_data})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/track-keywords', methods=['POST'])
def track_keywords():
    try:
        data = request.get_json()
        keywords = data.get('keywords', [])
        platforms = data.get('platforms', ['amazon', 'flipkart', 'meesho'])
        
        ranking_data = track_keyword_rankings(keywords, platforms)
        return jsonify({'success': True, 'data': ranking_data})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/market-trends', methods=['POST'])
def market_trends():
    try:
        data = request.get_json()
        category = data.get('category', 'General')
        timeframe = data.get('timeframe', '30d')
        
        trend_data = analyze_market_trends(category, timeframe)
        return jsonify({'success': True, 'data': trend_data})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)
