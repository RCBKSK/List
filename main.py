from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
import google.generativeai as genai
import json
import base64
import io
import pandas as pd
import openpyxl
from PIL import Image
import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime
import os
import re
import functools
from collections import defaultdict
from urllib.parse import urlparse
import tempfile

app = Flask(__name__)
CORS(app)

# Global variables
gemini_model = None
saved_templates = {}

# HSN Code to GST mapping
HSN_GST_MAPPING = {
    '8517': {'rate': 18, 'description': 'Telephone sets, telephones'},
    '9013': {'rate': 18, 'description': 'Liquid crystal devices'},
    '8471': {'rate': 18, 'description': 'Automatic data processing machines'},
    '6204': {'rate': 12, 'description': 'Women\'s suits, ensembles'},
    '6203': {'rate': 12, 'description': 'Men\'s suits, ensembles'},
    '6401': {'rate': 5, 'description': 'Waterproof footwear'},
    '6402': {'rate': 5, 'description': 'Other footwear'},
    '7323': {'rate': 18, 'description': 'Table, kitchen or other household articles'},
    '9404': {'rate': 18, 'description': 'Mattress supports; articles of bedding'},
    '6302': {'rate': 5, 'description': 'Bed linen, table linen, toilet linen'},
    '3304': {'rate': 18, 'description': 'Beauty or make-up preparations'},
    '3401': {'rate': 18, 'description': 'Soap; organic surface-active products'},
    '8414': {'rate': 18, 'description': 'Air or vacuum pumps, air compressors'},
    '8516': {'rate': 18, 'description': 'Electric instantaneous or storage water heaters'}
}

# Performance monitoring
api_stats = defaultdict(list)

def monitor_performance(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            success = True
        except Exception as e:
            result = None
            success = False
            raise e
        finally:
            end_time = time.time()
            api_stats[func.__name__].append({
                'duration': end_time - start_time,
                'success': success,
                'timestamp': time.time()
            })
        return result
    return wrapper

@app.route('/api/performance-stats', methods=['GET'])
def get_performance_stats():
    try:
        stats = {}
        for endpoint, calls in api_stats.items():
            if calls:
                avg_duration = sum(call['duration'] for call in calls) / len(calls)
                success_rate = sum(1 for call in calls if call['success']) / len(calls)
                stats[endpoint] = {
                    'avg_duration': round(avg_duration, 3),
                    'success_rate': round(success_rate * 100, 2),
                    'total_calls': len(calls)
                }
        return jsonify({'success': True, 'stats': stats})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
        rate = HSN_GST_MAPPING[hsn_clean]['rate']
        return rate, f"HSN {hsn_clean}: {rate * 100}% GST (Local)", {
            'hsnCode': hsn_clean,
            'gstRate': rate * 100,
            'description': 'Local mapping',
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

def configure_gemini_api(api_key):
    global gemini_model
    try:
        genai.configure(api_key=api_key)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash')
        return True
    except Exception as e:
        print(f"Error configuring Gemini API: {e}")
        return False

def analyze_image_with_gemini(image_data, product_info):
    global gemini_model
    if not gemini_model:
        raise Exception("Gemini API not configured")

    try:
        # Convert base64 to PIL Image
        image_data = image_data.split(',')[1] if ',' in image_data else image_data
        image_bytes = base64.b64decode(image_data)
        image = Image.open(io.BytesIO(image_bytes))

        prompt = f"""
        Analyze this product image and generate e-commerce listings for Amazon, Flipkart, and Meesho.

        Product Information:
        - Name: {product_info.get('name', 'Not provided')}
        - Brand: {product_info.get('brand', 'Not provided')}
        - Weight: {product_info.get('weight', 'Not provided')} kg
        - Dimensions: {product_info.get('length', 'Not provided')}x{product_info.get('width', 'Not provided')}x{product_info.get('height', 'Not provided')} cm

        Create listings optimized for each platform with appropriate titles, descriptions, bullet points, categories, and keywords.
        Return the response in this exact JSON format:

        {{
            "amazon": [
                {{
                    "version": 1,
                    "style": "Feature-rich",
                    "title": "Amazon-optimized title under 200 characters",
                    "bulletPoints": ["5 feature-focused bullet points under 250 chars each"],
                    "description": "Detailed 50-75 words description",
                    "category": "Suggested category",
                    "hsnCode": "HSN code preferably from 5% GST slab",
                    "keywords": ["comma-separated SEO keywords"]
                }},
                {{
                    "version": 2,
                    "style": "Problem-solving",
                    "title": "Problem-solving title under 200 characters",
                    "bulletPoints": ["5 solution-focused bullet points under 250 chars each"],
                    "description": "Solution-focused 50-75 words description",
                    "category": "Suggested category",
                    "hsnCode": "HSN code preferably from 5% GST slab",
                    "keywords": ["solution-focused SEO keywords"]
                }},
                {{
                    "version": 3,
                    "style": "Premium & Luxury",
                    "title": "Premium title under 200 characters",
                    "bulletPoints": ["5 premium bullet points under 250 chars each"],
                    "description": "Premium 50-75 words description",
                    "category": "Suggested category",
                    "hsnCode": "HSN code preferably from 5% GST slab",
                    "keywords": ["premium SEO keywords"]
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
                    "style": "Social & Shareable",
                    "title": "Social-media friendly title under 200 characters",
                    "bulletPoints": ["3-5 shareable bullet points under 250 chars each"],
                    "description": "Social-friendly 50-75 words description",
                    "category": "Suggested category",
                    "hsnCode": "HSN code preferably from 5% GST slab",
                    "keywords": ["social SEO keywords"]
                }}
            ]
        }}
        """

        response = gemini_model.generate_content([prompt, image])

        # Clean the response and extract JSON
        response_text = response.text.strip()
        if response_text.startswith('```json'):
            response_text = response_text[7:]
        if response_text.endswith('```'):
            response_text = response_text[:-3]

        return json.loads(response_text)

    except Exception as e:
        print(f"Error analyzing image: {e}")
        raise e

def calculate_shipping_cost(weight, length, width, height):
    volume = (length * width * height) / 5000  # Volumetric weight in kg
    chargeable_weight = max(weight, volume)

    if chargeable_weight <= 0.5:
        return 40
    elif chargeable_weight <= 1:
        return 60
    elif chargeable_weight <= 2:
        return 80
    else:
        return 80 + (chargeable_weight - 2) * 20

def get_gst_info(hsn_code):
    return HSN_GST_MAPPING.get(hsn_code, {'rate': 18, 'description': 'Other goods'})

def calculate_marketplace_pricing(cost_price, profit_margin, hsn_code, weight, length, width, height):
    gst_info = get_gst_info(hsn_code)
    gst_rate = gst_info['rate']

    shipping_cost = calculate_shipping_cost(weight, length, width, height)

    # Platform commission rates
    commission_rates = {
        'amazon': 15,  # 15% commission
        'flipkart': 12,  # 12% commission
        'meesho': 8    # 8% commission
    }

    pricing = {}

    for marketplace, commission_rate in commission_rates.items():
        # Calculate target profit
        target_profit = cost_price * (profit_margin / 100)

        # Calculate selling price before commission
        base_price = cost_price + target_profit + shipping_cost

        # Calculate platform commission
        platform_commission = base_price * (commission_rate / 100)

        # Calculate final selling price
        selling_price = base_price + platform_commission

        # Calculate GST
        gst = selling_price * (gst_rate / 100)

        # Calculate MRP (20% above selling price)
        mrp = selling_price * 1.2

        pricing[marketplace] = {
            'costPrice': round(cost_price, 2),
            'targetProfit': round(target_profit, 2),
            'shippingCost': round(shipping_cost, 2),
            'platformCommission': round(platform_commission, 2),
            'platformCommissionRate': f"{commission_rate}%",
            'gst': round(gst, 2),
            'gstRate': f"{gst_rate}%",
            'gstDescription': gst_info['description'],
            'sellingPrice': round(selling_price, 2),
            'mrp': round(mrp, 2),
            'hsnCode': hsn_code
        }

    return pricing

def scrape_product_info(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')

        product_data = {
            'title': '',
            'brand': '',
            'weight': '',
            'dimensions': {'length': '', 'width': '', 'height': ''}
        }

        # Amazon scraping
        if 'amazon' in url:
            title_elem = soup.find('span', {'id': 'productTitle'})
            if title_elem:
                product_data['title'] = title_elem.text.strip()

            # Try to find brand
            brand_elem = soup.find('tr', {'class': 'a-spacing-small'})
            if brand_elem:
                brand_text = brand_elem.find('td')
                if brand_text:
                    product_data['brand'] = brand_text.text.strip()

        # Flipkart scraping
        elif 'flipkart' in url:
            title_elem = soup.find('span', {'class': 'B_NuCI'})
            if title_elem:
                product_data['title'] = title_elem.text.strip()

        return product_data

    except Exception as e:
        print(f"Error scraping product: {e}")
        return None

def create_export_file(listings, pricing, format_type):
    if format_type == 'amazon':
        return create_amazon_excel(listings, pricing)
    elif format_type == 'flipkart':
        return create_flipkart_csv(listings, pricing)
    elif format_type == 'meesho':
        return create_meesho_excel(listings, pricing)

def create_amazon_excel(listings, pricing):
    df = pd.DataFrame()

    for listing in listings:
        row = {
            'Product Title': listing['title'],
            'Product Description': listing['description'],
            'Bullet Point 1': listing['bulletPoints'][0] if len(listing['bulletPoints']) > 0 else '',
            'Bullet Point 2': listing['bulletPoints'][1] if len(listing['bulletPoints']) > 1 else '',
            'Bullet Point 3': listing['bulletPoints'][2] if len(listing['bulletPoints']) > 2 else '',
            'Bullet Point 4': listing['bulletPoints'][3] if len(listing['bulletPoints']) > 3 else '',
            'Bullet Point 5': listing['bulletPoints'][4] if len(listing['bulletPoints']) > 4 else '',
            'Keywords': ', '.join(listing['keywords']) if isinstance(listing['keywords'], list) else listing['keywords'],
            'Category': listing['category'],
            'HSN Code': listing['hsnCode'],
            'MRP': pricing.get('amazon', {}).get('mrp', ''),
            'Selling Price': pricing.get('amazon', {}).get('sellingPrice', ''),
            'Version': listing.get('version', 1),
            'Style': listing.get('style', 'Standard')
        }
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)

    output = io.BytesIO()
    df.to_excel(output, index=False, engine='openpyxl')
    output.seek(0)
    return output

def create_flipkart_csv(listings, pricing):
    df = pd.DataFrame()

    for listing in listings:
        row = {
            'Product Name': listing['title'],
            'Product Description': listing['description'],
            'Key Features': '\n'.join(listing['bulletPoints']),
            'Keywords': ', '.join(listing['keywords']) if isinstance(listing['keywords'], list) else listing['keywords'],
            'Category': listing['category'],
            'HSN Code': listing['hsnCode'],
            'MRP': pricing.get('flipkart', {}).get('mrp', ''),
            'Selling Price': pricing.get('flipkart', {}).get('sellingPrice', ''),
            'Version': listing.get('version', 1),
            'Style': listing.get('style', 'Standard')
        }
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)

    output = io.StringIO()
    df.to_csv(output, index=False)
    return output

def create_meesho_excel(listings, pricing):
    df = pd.DataFrame()

    for listing in listings:
        row = {
            'Product Title': listing['title'],
            'Product Description': listing['description'],
            'Key Features': '\n'.join(listing['bulletPoints']),
            'Search Keywords': ', '.join(listing['keywords']) if isinstance(listing['keywords'], list) else listing['keywords'],
            'Category': listing['category'],
            'HSN Code': listing['hsnCode'],
            'MRP': pricing.get('meesho', {}).get('mrp', ''),
            'Selling Price': pricing.get('meesho', {}).get('sellingPrice', ''),
            'Version': listing.get('version', 1),
            'Style': listing.get('style', 'Standard')
        }
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)

    output = io.BytesIO()
    df.to_excel(output, index=False, engine='openpyxl')
    output.seek(0)
    return output

def analyze_seo_score(title, description, keywords, category):
    score = 0
    feedback = []

    # Title analysis
    if len(title) >= 50 and len(title) <= 200:
        score += 25
    else:
        feedback.append("Title should be between 50-200 characters")

    # Description analysis
    if len(description.split()) >= 50:
        score += 25
    else:
        feedback.append("Description should have at least 50 words")

    # Keywords analysis
    if isinstance(keywords, list):
        keyword_count = len(keywords)
    else:
        keyword_count = len(keywords.split(','))

    if keyword_count >= 5:
        score += 25
    else:
        feedback.append("Should have at least 5 keywords")

    # Category analysis
    if category:
        score += 25
    else:
        feedback.append("Category is required")

    if not feedback:
        feedback.append("SEO optimization looks good! Consider adding high-volume keywords")

    return {
        'score': score,
        'grade': 'A' if score >= 90 else 'B' if score >= 70 else 'C' if score >= 50 else 'D',
        'feedback': feedback,
        'improvements': []
    }

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

# Routes
@app.route('/')
def index():
    with open('templates/index.html', 'r') as f:
        return f.read()

@app.route('/api/configure-gemini', methods=['POST'])
def configure_gemini():
    try:
        data = request.get_json()
        api_key = data.get('apiKey')

        if not api_key:
            return jsonify({'error': 'API key is required'}), 400

        if configure_gemini_api(api_key):
            return jsonify({'success': True, 'message': 'Gemini API configured successfully'})
        else:
            return jsonify({'error': 'Failed to configure Gemini API'}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/generate-listing', methods=['POST'])
def generate_listing():
    try:
        data = request.get_json()
        image_data = data.get('image')
        product_info = data.get('productInfo', {})

        if not image_data:
            return jsonify({'error': 'Image is required'}), 400

        listings = analyze_image_with_gemini(image_data, product_info)
        return jsonify({'success': True, 'data': listings})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/calculate-price', methods=['POST'])
def calculate_price():
    try:
        data = request.get_json()
        cost_price = data.get('costPrice')
        profit_margin = data.get('profitMargin', 42.5)
        hsn_code = data.get('hsnCode')
        weight = data.get('weight', 0)
        length = data.get('length', 0)
        width = data.get('width', 0)
        height = data.get('height', 0)

        if not cost_price or not hsn_code:
            return jsonify({'error': 'Cost price and HSN code are required'}), 400

        pricing = calculate_marketplace_pricing(
            float(cost_price), profit_margin, hsn_code,
            float(weight), float(length), float(width), float(height)
        )

        return jsonify({'success': True, 'data': pricing})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/scrape-product', methods=['POST'])
def scrape_product():
    try:
        data = request.get_json()
        url = data.get('url')

        if not url:
            return jsonify({'error': 'URL is required'}), 400

        product_data = scrape_product_info(url)

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
            return jsonify({'error': 'Failed to scrape product data'}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/<format_type>', methods=['POST'])
def export_listing(format_type):
    try:
        data = request.get_json()
        listings = data.get('listing', [])
        pricing = data.get('pricing', {})

        if not listings:
            return jsonify({'error': 'Listing data is required'}), 400

        file_data = create_export_file(listings, pricing, format_type)

        if format_type == 'flipkart':
            return send_file(
                io.BytesIO(file_data.getvalue().encode()),
                mimetype='text/csv',
                as_attachment=True,
                download_name=f'{format_type}_listing.csv'
            )
        else:
            return send_file(
                file_data,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name=f'{format_type}_listing.xlsx'
            )

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/analyze-seo', methods=['POST'])
def analyze_seo():
    try:
        data = request.get_json()
        title = data.get('title', '')
        description = data.get('description', '')
        keywords = data.get('keywords', [])
        category = data.get('category', '')

        analysis = analyze_seo_score(title, description, keywords, category)
        return jsonify({'success': True, 'data': analysis})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/save-template', methods=['POST'])
def save_template():
    try:
        data = request.get_json()
        template_name = data.get('templateName')
        template_data = data.get('templateData')

        if not template_name or not template_data:
            return jsonify({'error': 'Template name and data required'}), 400

        saved_templates[template_name] = {
            'data': template_data,
            'created_at': datetime.now().isoformat(),
            'usage_count': 0
        }

        return jsonify({'success': True, 'message': 'Template saved successfully'})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/get-templates', methods=['GET'])
def get_templates():
    try:
        return jsonify({'success': True, 'templates': saved_templates})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/use-template', methods=['POST'])
def use_template():
    try:
        data = request.get_json()
        template_name = data.get('templateName')

        if template_name not in saved_templates:
            return jsonify({'error': 'Template not found'}), 404

        saved_templates[template_name]['usage_count'] += 1
        return jsonify({'success': True, 'data': saved_templates[template_name]['data']})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)