
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

app = Flask(__name__)
CORS(app)

# Configure Gemini AI (will be set by user input)
genai_api_key = None

def configure_gemini(api_key):
    global genai_api_key
    genai_api_key = api_key
    genai.configure(api_key=api_key)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/generate-listing', methods=['POST'])
def generate_listing():
    try:
        data = request.get_json()
        
        if not genai_api_key:
            return jsonify({'error': 'Gemini API key not configured'}), 400
        
        # Get image data
        image_data = data.get('image')
        if not image_data:
            return jsonify({'error': 'No image provided'}), 400
        
        # Remove data URL prefix if present
        if 'base64,' in image_data:
            image_data = image_data.split('base64,')[1]
        
        # Decode base64 image
        image_bytes = base64.b64decode(image_data)
        image = Image.open(io.BytesIO(image_bytes))
        
        # Get additional product info
        product_info = data.get('productInfo', {})
        product_name = product_info.get('name', '')
        brand = product_info.get('brand', '')
        dimensions = product_info.get('dimensions', '')
        cost_price = product_info.get('costPrice', 0)
        
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
        model = genai.GenerativeModel('gemini-pro-vision')
        response = model.generate_content([prompt, image])
        
        # Parse the response
        try:
            # Extract JSON from response
            response_text = response.text
            if '```json' in response_text:
                json_start = response_text.find('```json') + 7
                json_end = response_text.find('```', json_start)
                response_text = response_text[json_start:json_end]
            elif '{' in response_text and '}' in response_text:
                json_start = response_text.find('{')
                json_end = response_text.rfind('}') + 1
                response_text = response_text[json_start:json_end]
            
            listing_data = json.loads(response_text)
        except:
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
        
        return jsonify({'success': True, 'data': listing_data})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/calculate-price', methods=['POST'])
def calculate_price():
    try:
        data = request.get_json()
        
        cost_price = float(data.get('costPrice', 0))
        profit_margin = float(data.get('profitMargin', 42.5)) / 100
        gst_rate = 0.05  # Fixed 5% GST
        platform_commission = float(data.get('platformCommission', 15)) / 100
        
        # Calculate dimensions and shipping
        length = float(data.get('length', 0))
        width = float(data.get('width', 0))
        height = float(data.get('height', 0))
        
        # Volumetric weight calculation
        volumetric_weight = (length * width * height) / 5000 if all([length, width, height]) else 0
        
        # Shipping cost calculation
        if volumetric_weight <= 0.5:
            shipping_cost = 100
        elif volumetric_weight <= 1:
            shipping_cost = 140
        elif volumetric_weight <= 1.5:
            shipping_cost = 180
        else:
            shipping_cost = 220
        
        # Price calculation
        # Cost + GST + Profit + Platform Commission + Shipping
        cost_with_gst = cost_price * (1 + gst_rate)
        target_profit = cost_price * profit_margin
        
        # Calculate selling price considering all costs
        base_price = cost_with_gst + target_profit + shipping_cost
        final_price = base_price / (1 - platform_commission)
        
        mrp = final_price * 1.2  # 20% above selling price for MRP
        
        price_breakdown = {
            'costPrice': cost_price,
            'gst': cost_price * gst_rate,
            'targetProfit': target_profit,
            'shippingCost': shipping_cost,
            'platformCommission': final_price * platform_commission,
            'sellingPrice': round(final_price, 2),
            'mrp': round(mrp, 2),
            'volumetricWeight': round(volumetric_weight, 2)
        }
        
        return jsonify({'success': True, 'data': price_breakdown})
        
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
