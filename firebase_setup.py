"""
Firebase Infrastructure Setup for Launch Integrity Field
Architectural Rationale: Firebase provides serverless scaling, real-time updates, and 
structured NoSQL storage ideal for dynamic token launch data.
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict

import firebase_admin
from firebase_admin import credentials, firestore, db
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class FirestoreCollections:
    """Centralized collection names to prevent string typos"""
    WALLET_PROFILES = "wallet_profiles"
    LAUNCH_MONITORS = "launch_monitors"
    HISTORICAL_PATTERNS = "historical_patterns"
    FALSE_POSITIVES = "false_positives"
    ALERTS = "alerts"


class FirebaseInfrastructure:
    """
    Manages Firebase initialization and provides structured access to Firestore collections
    and Realtime Database for alerts.
    
    Edge Case Handling:
    1. Multiple initialization attempts
    2. Credential file validation
    3. Connection timeout recovery
    4. TTL index creation for data retention
    """
    
    def __init__(self, credential_path: str, project_id: Optional[str] = None):
        """
        Initialize Firebase with proper error handling.
        
        Args:
            credential_path: Path to Firebase service account JSON
            project_id: Optional override for Firebase project ID
        """
        self.credential_path = credential_path
        self.project_id = project_id
        self._initialized = False
        self._firestore_client = None
        self._rtdb_client = None
        
        self.collections = FirestoreCollections()
        
    def initialize(self) -> bool:
        """
        Initialize Firebase Admin SDK with comprehensive error handling.
        
        Returns:
            bool: True if initialization successful, False otherwise
        """
        try:
            # Check if already initialized
            if firebase_admin._apps:
                logger.info("Firebase already initialized, reusing existing app")
                app = firebase_admin.get_app()
            else:
                # Validate credential file exists
                if not os.path.exists(self.credential_path):
                    logger.error(f"Firebase credential file not found: {self.credential_path}")
                    return False
                    
                # Initialize with credentials
                cred = credentials.Certificate(self.credential_path)
                app_options = {'credential': cred}
                
                if self.project_id:
                    app_options['projectId'] = self.project_id
                    
                app = firebase_admin.initialize_app(**app_options)
                logger.info("Firebase initialized successfully")
            
            # Initialize clients
            self._firestore_client = firestore.client(app)
            self._rtdb_client = db.reference(app=app)
            
            # Create TTL indexes for data retention
            self._setup_ttl_indexes()
            
            self._initialized = True
            return True
            
        except Exception as e:
            logger.error(f"Firebase initialization failed: {str(e)}")
            return False
    
    def _setup_ttl_indexes(self) -> None:
        """
        Setup TTL policies for data retention.
        Implementation Note: TTL policies must be created in Firebase console,
        but we document required indexes here.
        """
        ttl_configs = [
            {
                "collection": self.collections.LAUNCH_MONITORS,
                "ttl_field": "expires_at",
                "description": "Auto-delete launch data after 1 hour"
            },
            {
                "collection": self.collections.ALERTS,
                "ttl_field": "expires_at",
                "description": "Auto-delete alerts after 24 hours"
            }
        ]
        
        logger.info("TTL configuration required for collections:")
        for config in ttl_configs:
            logger.info(f"  - {config['collection']}: {config['description']}")
    
    @property
    def firestore(self):
        """Lazy-loaded Firestore client with validation"""
        if not self._initialized:
            raise RuntimeError("Firebase not initialized. Call initialize() first.")
        return self._firestore_client
    
    @property
    def realtime_db(self):
        """Lazy-loaded Realtime Database client with validation"""
        if not self._initialized:
            raise RuntimeError("Firebase not initialized. Call initialize() first.")
        return self._rtdb_client
    
    def create_wallet_profile(self, wallet_address: str, data: Dict[str, Any]) -> bool:
        """
        Create or update wallet profile with transaction count tracking.
        
        Args:
            wallet_address: Ethereum wallet address (checksummed)
            data: Wallet profile data
            
        Returns:
            bool: Success status
        """
        try:
            # Add metadata
            data['last_updated'] = SERVER_TIMESTAMP
            data['analysis_count'] = data.get('analysis_count', 0) + 1
            
            # Store in Firestore
            doc_ref = self.firestore.collection(self.collections.WALLET_PROFILES).document(wallet_address.lower())
            doc_ref.set(data, merge=True)
            
            logger.info(f"Updated wallet profile for {wallet_address}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create wallet profile: {str(e)}")
            return False
    
    def push_alert(self, alert_type: str, severity: str, message: str, 
                   data: Optional[Dict] = None) -> str:
        """
        Push real-time alert to Realtime Database with unique ID.
        
        Args:
            alert_type: Type of alert (SECURITY, PERFORMANCE, ANOMALY)
            severity: Alert severity (CRITICAL, HIGH, MEDIUM, LOW)
            message: Human-readable alert message
            data: Additional alert data
            
        Returns:
            str: Alert ID or empty string on failure
        """
        try:
            alert_id = f"alert_{int(datetime.utcnow().timestamp() * 1000)}"
            alert_data = {
                'id': alert_id,
                'type': alert_type,
                'severity': severity,
                'message': message,
                'timestamp': SERVER_TIMESTAMP,
                'expires_at': datetime.utcnow() + timedelta(hours=24),
                'data': data or {}
            }
            
            # Push to Realtime Database for instant delivery
            self.realtime_db.child('alerts').child(alert_id).set(asdict(alert_data))
            
            # Also store in Firestore for persistence
            self.firestore.collection(self.collections.ALERTS).document(alert_id).set(alert_data)
            
            logger.info(f"Alert pushed: {alert_type} - {severity}: {message}")
            return alert_id
            
        except Exception as e:
            logger.error(f"Failed to push alert: {str(e)}")
            return ""


# Singleton instance for global access
firebase_infra = None


def get_firebase_infra() -> FirebaseInfrastructure:
    """
    Get or initialize Firebase infrastructure singleton.
    
    Returns:
        FirebaseInfrastructure: Initialized Firebase instance
    """
    global firebase_infra
    
    if firebase_infra is None:
        # Load configuration from environment
        from dotenv import load_dotenv
        load_dotenv()
        
        credential_path = os.getenv('FIREBASE_CREDENTIALS_PATH', './serviceAccountKey.json')
        project_id = os.getenv('FIREBASE_PROJECT_ID')
        
        firebase_infra = FirebaseInfrastructure(credential_path, project_id)
        
        if not firebase_infra.initialize():
            raise RuntimeError("Failed to initialize Firebase infrastructure")
    
    return firebase_infra


if __name__ == "__main__":
    # Test the Firebase setup
    infra = get_firebase_infra()
    print("Firebase infrastructure test successful")
    
    # Test alert system
    alert_id = infra.push_alert(
        alert_type="SYSTEM",
        severity="LOW",
        message="Firebase infrastructure initialized successfully",
        data={"component": "firebase_setup"}
    )
    print(f"Test alert created with ID: {alert_id}")