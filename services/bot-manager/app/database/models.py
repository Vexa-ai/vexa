from sqlalchemy import Column, String, Text, DateTime, Integer, create_engine, ForeignKey, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import datetime
import os

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=True)
    email = Column(String(255), nullable=True, index=True)
    created_at = Column(DateTime, server_default=func.now())
    max_concurrent_bots = Column(Integer, nullable=False, server_default='1', default=1)
    
    meetings = relationship("Meeting", back_populates="user")
    
    def __repr__(self):
        return f"<User(id='{self.id}', name='{self.name}')>"

class Meeting(Base):
    __tablename__ = 'meetings'
    
    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey('users.id'), nullable=False)
    title = Column(String, nullable=True)
    start_time = Column(DateTime, default=datetime.datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    
    user = relationship("User", back_populates="meetings")
    transcriptions = relationship("Transcription", back_populates="meeting")
    
    def __repr__(self):
        return f"<Meeting(id='{self.id}', user_id='{self.user_id}', title='{self.title}')>"

class Transcription(Base):
    __tablename__ = 'transcriptions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    meeting_id = Column(String, ForeignKey('meetings.id'), nullable=False)
    speaker = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    confidence = Column(Integer, nullable=True)  # Can store confidence score from transcription service
    
    meeting = relationship("Meeting", back_populates="transcriptions")
    
    def __repr__(self):
        return f"<Transcription(id={self.id}, meeting_id='{self.meeting_id}', timestamp='{self.timestamp}')>"

# Database connection
def get_engine():
    """Get SQLAlchemy engine using environment variables for configuration"""
    db_host = os.getenv("DB_HOST", "postgres")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "vexa")
    db_user = os.getenv("DB_USER", "postgres")
    db_password = os.getenv("DB_PASSWORD", "postgres")
    
    connection_string = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    return create_engine(connection_string)

def get_session():
    """Create a new database session"""
    engine = get_engine()
    Session = sessionmaker(bind=engine)
    return Session()

def init_db():
    """Initialize database with tables"""
    engine = get_engine()
    Base.metadata.create_all(engine) 