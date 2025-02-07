# Vexa - meeting notetaker and knowledge chat for teams and individuals

NOTE: The service is live and running and currently is getting prepared for open sourcing. Planned opening to the public - Late February 2025.

🌟 Go try it out and use for free at [vexa.ai](https://vexa.ai) to get a flavor of the service.


Vexa is a powerful, distributed system for real-time audio processing, transcription, and analysis. It's designed to handle live audio streams from meetings and conversations, providing instant transcription and insights.

## 🚀 Features

- Real-time audio transcription and speaker assignment
- Chrome extension with contextual support
- Real-time Knowledge Extraction from meetings and documents 
- Chat fully aware of what is going on in the company 
- Fine-grained control over data access
- Scalable microservices architecture

## 🎯 Key Use Cases

- Team Meetings & Collaboration
- Knowledge Management
- Company-wide Information Discovery
- Project Documentation
- Customer Interactions

## 🏗 System Architecture

![System Architecture](assets/architecture-placeholder.png)

### User Interfaces

#### Google Chrome Extension
- Real-time transcripts cleaned for readability
- Interactive UI for contextual support (single click actions)
- Contextual chat with access to the whole company knowledge base

#### Meeting and Chat Interface
- Chat and research across company data, filtered by:
  - Speaker
  - Project
  - Meeting
- Advanced search capabilities for:
  - People
  - Companies
  - Products
  - And more

### Backend Services

#### 1. Streamqueue Service
- Handles audio stream collection from the client browser

#### 2. Audio Service
- Performs speech-to-text conversion using Whisper
- Uses load balancer to distribute between distributed model instances (coming soon)
- Handles transcription-speaker association

#### 3. Engine Service
- Core business logic
- Knowledge extraction and accessibility

## 🛠 Technology Stack

### Frontend
- React
- Chrome Extension APIs

### Backend
- Python 3.12+
- FastAPI

### Data Storage & Search
- Redis
- PostgreSQL 
- Qdrant
- Elastic Search

### Infrastructure
- Docker and Docker Compose
- Kubernetes (coming soon)

## 🔒 Security & Privacy

- End-to-end encryption for sensitive data
- Fine-grained access control
- GDPR compliant
- SOC 2 compliance (in progress)

## 📈 Current Status

- ✅ Live and operational
- 🚧 Preparing for open source release
- 📅 Public release: Late February 2025
- 🆓 Free tier available at [vexa.ai](https://vexa.ai)

## 🤝 Community & Support

- [Documentation](https://docs.vexa.ai)
- [Community Forum](https://community.vexa.ai)
- [Feature Requests](https://feedback.vexa.ai)
- Email: support@vexa.ai

## 📝 License

Coming with public release - Late February 2025

---

Made with ❤️ by the Vexa Team

