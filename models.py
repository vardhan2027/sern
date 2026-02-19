from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta

db = SQLAlchemy()

# Association table for request-contributor matching
request_contributors = db.Table('request_contributors',
    db.Column('request_id', db.Integer, db.ForeignKey('emergency_request.id'), primary_key=True),
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('status', db.String(20), default='pending'),  # pending, accepted, completed, declined
    db.Column('responded_at', db.DateTime)
)


class User(UserMixin, db.Model):
    """User model for all stakeholders - individuals and organizations"""
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    
    # Role: hospital, blood_bank, ngo, ambulance, volunteer, donor
    role = db.Column(db.String(20), nullable=False)
    
    # Location
    city = db.Column(db.String(100), nullable=False)
    district = db.Column(db.String(100))
    address = db.Column(db.Text)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    
    # For blood donors
    blood_group = db.Column(db.String(5))  # A+, A-, B+, B-, AB+, AB-, O+, O-
    last_donation_date = db.Column(db.Date)
    
    # Availability toggle
    is_available = db.Column(db.Boolean, default=True)
    
    # Trust metrics
    iri_score = db.Column(db.Float, default=50.0)  # Individual Reliability Index (0-100)
    ecc_credits = db.Column(db.Integer, default=0)  # Emergency Contribution Credits (for orgs)
    
    # Statistics
    total_requests_received = db.Column(db.Integer, default=0)
    total_requests_fulfilled = db.Column(db.Integer, default=0)
    total_requests_declined = db.Column(db.Integer, default=0)
    response_time_avg = db.Column(db.Float, default=0.0)  # in minutes
    
    # Verification status (for organizations)
    is_verified = db.Column(db.Boolean, default=False)
    verification_document = db.Column(db.String(255))
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    resources = db.relationship('Resource', backref='owner', lazy='dynamic')
    requests_created = db.relationship('EmergencyRequest', backref='requester', lazy='dynamic', 
                                       foreign_keys='EmergencyRequest.requester_id')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def can_donate_blood(self):
        """Check if donor is eligible (56 days since last donation)"""
        if not self.last_donation_date:
            return True
        return datetime.now().date() - self.last_donation_date >= timedelta(days=56)
    
    def is_organization(self):
        return self.role in ['hospital', 'blood_bank', 'ngo', 'ambulance']
    
    def update_iri(self, fulfilled=True, response_time_minutes=0):
        """Update Individual Reliability Index based on response behavior"""
        self.total_requests_received += 1
        
        if fulfilled:
            self.total_requests_fulfilled += 1
            # Positive adjustment (max +5 per fulfillment)
            fulfillment_bonus = min(5, 100 - self.iri_score)
            # Faster response = higher bonus
            time_bonus = max(0, 2 - (response_time_minutes / 30))  # Up to +2 for fast response
            self.iri_score = min(100, self.iri_score + fulfillment_bonus + time_bonus)
        else:
            self.total_requests_declined += 1
            # Negative adjustment
            self.iri_score = max(0, self.iri_score - 3)
        
        # Update average response time
        if response_time_minutes > 0:
            total_responses = self.total_requests_fulfilled + self.total_requests_declined
            self.response_time_avg = (
                (self.response_time_avg * (total_responses - 1) + response_time_minutes) / total_responses
            )


class Resource(db.Model):
    """Resources that can be offered (blood, plasma, oxygen, ambulance, volunteer service)"""
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Resource type: blood, plasma, oxygen, ambulance, volunteer
    resource_type = db.Column(db.String(20), nullable=False)
    
    # For blood/plasma
    blood_group = db.Column(db.String(5))
    units_available = db.Column(db.Integer, default=1)
    
    # For ambulance
    vehicle_type = db.Column(db.String(50))  # basic, advanced, icu
    vehicle_number = db.Column(db.String(20))
    
    # For oxygen
    oxygen_type = db.Column(db.String(20))  # cylinder, concentrator
    capacity_liters = db.Column(db.Integer)
    
    # Availability
    is_available = db.Column(db.Boolean, default=True)
    available_from = db.Column(db.DateTime)
    available_until = db.Column(db.DateTime)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmergencyRequest(db.Model):
    """Emergency resource requests"""
    id = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Request details
    resource_type = db.Column(db.String(20), nullable=False)  # blood, plasma, oxygen, ambulance, volunteer
    blood_group = db.Column(db.String(5))  # For blood/plasma requests
    units_needed = db.Column(db.Integer, default=1)
    
    # Urgency: critical, urgent, normal
    urgency = db.Column(db.String(20), nullable=False, default='normal')
    
    # Location
    city = db.Column(db.String(100), nullable=False)
    district = db.Column(db.String(100))
    hospital_name = db.Column(db.String(200))
    address = db.Column(db.Text)
    
    # Patient info (anonymized)
    patient_age = db.Column(db.Integer)
    patient_condition = db.Column(db.Text)
    
    # Status: open, matching, partially_fulfilled, fulfilled, cancelled, expired
    status = db.Column(db.String(20), default='open')
    
    # Matching
    search_radius_km = db.Column(db.Integer, default=25)
    auto_expanded = db.Column(db.Boolean, default=False)  # True if radius was auto-expanded
    
    # Tracking
    units_fulfilled = db.Column(db.Integer, default=0)
    fulfilled_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    fulfilled_at = db.Column(db.DateTime)
    expires_at = db.Column(db.DateTime)
    
    # Verification
    is_verified = db.Column(db.Boolean, default=False)
    verified_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    
    # Relationships
    responses = db.relationship('RequestResponse', backref='request', lazy='dynamic')
    fulfilled_by = db.relationship('User', foreign_keys=[fulfilled_by_id])


class RequestResponse(db.Model):
    """Track responses to emergency requests"""
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('emergency_request.id'), nullable=False)
    responder_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Status: notified, accepted, declined, completed, no_response
    status = db.Column(db.String(20), default='notified')
    
    # Units offered/provided
    units_offered = db.Column(db.Integer, default=1)
    units_provided = db.Column(db.Integer, default=0)
    
    # Timestamps
    notified_at = db.Column(db.DateTime, default=datetime.utcnow)
    responded_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    
    # Feedback
    requester_rating = db.Column(db.Integer)  # 1-5
    responder_notes = db.Column(db.Text)
    
    responder = db.relationship('User', backref='responses')


class ContributionLog(db.Model):
    """Log all contributions for ECC calculation"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    request_id = db.Column(db.Integer, db.ForeignKey('emergency_request.id'))
    
    # Contribution type: fulfillment, verification, referral, partnership
    contribution_type = db.Column(db.String(20), nullable=False)
    
    # ECC earned
    ecc_earned = db.Column(db.Integer, default=0)
    
    # Details
    description = db.Column(db.Text)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='contributions')


class Partnership(db.Model):
    """Track partnerships between organizations"""
    id = db.Column(db.Integer, primary_key=True)
    organization_a_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    organization_b_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Partnership type: formal, informal, network
    partnership_type = db.Column(db.String(20), default='network')
    
    # Status: pending, active, inactive
    status = db.Column(db.String(20), default='pending')
    
    # Collaboration metrics
    successful_collaborations = db.Column(db.Integer, default=0)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    org_a = db.relationship('User', foreign_keys=[organization_a_id])
    org_b = db.relationship('User', foreign_keys=[organization_b_id])
