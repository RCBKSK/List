
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

app = Flask(__name__)
CORS(app)

# Configure Gemini AI (will be set by user input)
genai_api_key = None

def configure_gemini(api_key):
    global genai_api_key
    genai_api_key = api_key
    genai.configure(api_key=api_key)

def scrape_product_data(url):
    """Scrape product dimensions and weight from e-commerce URLs"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        domain = urlparse(url).netloc.lower()
        
        product_data = {
            'weight': None,
            'dimensions': {'length': None, 'width': None, 'height': None},
            'brand': None,
            'title': None
        }
        
        # Amazon scraping
        if 'amazon.' in domain:
            product_data = scrape_amazon(soup)
        # Flipkart scraping
        elif 'flipkart.' in domain:
            product_data = scrape_flipkart(soup)
        # Meesho scraping
        elif 'meesho.' in domain:
            product_data = scrape_meesho(soup)
        # Generic scraping
        else:
            product_data = scrape_generic(soup)
            
        return product_data
        
    except Exception as e:
        print(f"Error scraping product data: {e}")
        return None

def scrape_amazon(soup):
    """Scrape Amazon product page"""
    data = {'weight': None, 'dimensions': {'length': None, 'width': None, 'height': None}, 'brand': None, 'title': None}
    
    # Title
    title_elem = soup.find('span', {'id': 'productTitle'})
    if title_elem:
        data['title'] = title_elem.get_text().strip()
    
    # Brand
    brand_elem = soup.find('tr', class_='a-spacing-small po-brand')
    if brand_elem:
        brand_span = brand_elem.find('span', class_='a-offscreen')
        if brand_span:
            data['brand'] = brand_span.get_text().strip()
    
    # Product details table
    detail_table = soup.find('table', {'id': 'productDetails_detailBullets_sections1'})
    if detail_table:
        rows = detail_table.find_all('tr')
        for row in rows:
            label = row.find('th')
            value = row.find('td')
            if label and value:
                label_text = label.get_text().strip().lower()
                value_text = value.get_text().strip()
                
                if 'weight' in label_text:
                    weight_match = re.search(r'(\d+(?:\.\d+)?)\s*(kg|grams?|g)\b', value_text, re.IGNORECASE)
                    if weight_match:
                        weight_val = float(weight_match.group(1))
                        unit = weight_match.group(2).lower()
                        if unit in ['g', 'gram', 'grams']:
                            weight_val = weight_val / 1000  # Convert to kg
                        data['weight'] = weight_val
                
                if 'dimension' in label_text:
                    # Extract dimensions (L x W x H)
                    dim_match = re.findall(r'(\d+(?:\.\d+)?)', value_text)
                    if len(dim_match) >= 3:
                        data['dimensions'] = {
                            'length': float(dim_match[0]),
                            'width': float(dim_match[1]),
                            'height': float(dim_match[2])
                        }
    
    return data

def scrape_flipkart(soup):
    """Scrape Flipkart product page"""
    data = {'weight': None, 'dimensions': {'length': None, 'width': None, 'height': None}, 'brand': None, 'title': None}
    
    # Title
    title_elem = soup.find('span', class_='B_NuCI')
    if title_elem:
        data['title'] = title_elem.get_text().strip()
    
    # Specifications table
    spec_tables = soup.find_all('table', class_='_14cfVK')
    for table in spec_tables:
        rows = table.find_all('tr')
        for row in rows:
            cells = row.find_all('td')
            if len(cells) >= 2:
                label = cells[0].get_text().strip().lower()
                value = cells[1].get_text().strip()
                
                if 'weight' in label:
                    weight_match = re.search(r'(\d+(?:\.\d+)?)\s*(kg|grams?|g)\b', value, re.IGNORECASE)
                    if weight_match:
                        weight_val = float(weight_match.group(1))
                        unit = weight_match.group(2).lower()
                        if unit in ['g', 'gram', 'grams']:
                            weight_val = weight_val / 1000
                        data['weight'] = weight_val
                
                if 'dimension' in label:
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
        Analyze this product image and generate an e-commerce listing for Indian marketplaces (Amazon, Flipkart, Meesho).
        
        Additional product information:
        - Product Name: {product_name}
        - Brand: {brand}
        - Dimensions: {dimensions}
        - Cost Price: ₹{cost_price}
        
        Please provide a JSON response with the following structure:
        {{
            "title": "Product title under 200 characters",
            "bulletPoints": ["3-5 bullet points under 250 chars each"],
            "description": "50-75 words description",
            "category": "Suggested category",
            "hsnCode": "HSN code preferably from 5% GST slab",
            "keywords": ["comma-separated SEO keywords"]
        }}
        
        Make the content appealing for Indian customers, include relevant features, benefits, and specifications.
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
                "title": f"{brand} {product_name}".strip() or "Product Title",
                "bulletPoints": [
                    "High quality product",
                    "Suitable for daily use",
                    "Durable and long-lasting"
                ],
                "description": "Quality product with excellent features and reliable performance for everyday use.",
                "category": "General",
                "hsnCode": "9999",
                "keywords": ["quality", "durable", "reliable"]
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
        
        cost_price = float(data.get('costPrice', 0))
        profit_margin = float(data.get('profitMargin', 42.5)) / 100
        gst_rate = 0.05  # Fixed 5% GST
        marketplace = data.get('marketplace', 'amazon')
        
        # Get weight and dimensions
        weight = float(data.get('weight', 0))
        length = float(data.get('length', 0))
        width = float(data.get('width', 0))
        height = float(data.get('height', 0))
        
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
        listing = data.get('listing', {})
        pricing = data.get('pricing', {})
        
        # Create temporary file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{format}')
        
        if format == 'amazon':
            # Amazon Flat File format
            amazon_data = {
                'Product Title': [listing.get('title', '')],
                'Product Description': [listing.get('description', '')],
                'Bullet Point 1': [listing.get('bulletPoints', [''])[0] if listing.get('bulletPoints') else ''],
                'Bullet Point 2': [listing.get('bulletPoints', ['', ''])[1] if len(listing.get('bulletPoints', [])) > 1 else ''],
                'Bullet Point 3': [listing.get('bulletPoints', ['', '', ''])[2] if len(listing.get('bulletPoints', [])) > 2 else ''],
                'Standard Price': [pricing.get('mrp', 0)],
                'Sale Price': [pricing.get('sellingPrice', 0)],
                'Keywords': [', '.join(listing.get('keywords', []))],
                'HSN Code': [listing.get('hsnCode', '')]
            }
            df = pd.DataFrame(amazon_data)
            df.to_excel(temp_file.name, index=False)
            
        elif format == 'flipkart':
            # Flipkart CSV format
            flipkart_data = {
                'Product Name': [listing.get('title', '')],
                'Product Description': [listing.get('description', '')],
                'Key Features': ['; '.join(listing.get('bulletPoints', []))],
                'MRP': [pricing.get('mrp', 0)],
                'Selling Price': [pricing.get('sellingPrice', 0)],
                'Category': [listing.get('category', '')],
                'HSN': [listing.get('hsnCode', '')],
                'Keywords': [', '.join(listing.get('keywords', []))]
            }
            df = pd.DataFrame(flipkart_data)
            df.to_csv(temp_file.name, index=False)
            
        elif format == 'meesho':
            # Meesho Excel format
            meesho_data = {
                'Product Title': [listing.get('title', '')],
                'Product Description': [listing.get('description', '')],
                'Features': ['\n'.join(listing.get('bulletPoints', []))],
                'MRP': [pricing.get('mrp', 0)],
                'Supplier Price': [pricing.get('sellingPrice', 0)],
                'Category': [listing.get('category', '')],
                'HSN Code': [listing.get('hsnCode', '')],
                'Tags': [', '.join(listing.get('keywords', []))]
            }
            df = pd.DataFrame(meesho_data)
            df.to_excel(temp_file.name, index=False)
        
        return send_file(temp_file.name, as_attachment=True, 
                        download_name=f'product_listing_{format}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.{"xlsx" if format in ["amazon", "meesho"] else "csv"}')
        
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
