from flask import Flask, request, jsonify
import json

app = Flask(__name__)

@app.route('/api/webhook', methods=['POST'])
def webhook():
    print("=== WEBHOOK RECEIVED ===")
    print(f"Headers: {dict(request.headers)}")
    print(f"JSON Payload: {json.dumps(request.get_json(), indent=2)}")
    print("========================")
    
    return jsonify({"status": "received", "message": "Webhook processed successfully"}), 200

@app.route('/', methods=['GET'])
def health():
    return "Webhook server is running!", 200

if __name__ == '__main__':
    print("Starting webhook test server on port 5000...")
    app.run(host='0.0.0.0', port=5000, debug=True) 