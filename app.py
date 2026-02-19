from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from config import Config
from models import db, User, Resource, EmergencyRequest, RequestResponse, ContributionLog, Partnership
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Blood compatibility matrix
BLOOD_COMPATIBILITY = {
    'O-': ['O-', 'O+', 'A-', 'A+', 'B-', 'B+', 'AB-', 'AB+'],
    'O+': ['O+', 'A+', 'B+', 'AB+'],
    'A-': ['A-', 'A+', 'AB-', 'AB+'],
    'A+': ['A+', 'AB+'],
    'B-': ['B-', 'B+', 'AB-', 'AB+'],
    'B+': ['B+', 'AB+'],
    'AB-': ['AB-', 'AB+'],
    'AB+': ['AB+']
}

RARE_BLOOD_GROUPS = ['AB-', 'B-', 'A-', 'O-']

@login_manager.user_loader
def load_user(id):
    return User.query.get(int(id))


def org_required(f):
    """Decorator to require organization role"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_organization():
            flash('This action requires an organization account.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


# ============== AUTHENTICATION ==============

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    # Get stats for landing page
    total_users = User.query.count()
    total_requests = EmergencyRequest.query.count()
    fulfilled_requests = EmergencyRequest.query.filter_by(status='fulfilled').count()
    
    return render_template('index.html', 
                          total_users=total_users,
                          total_requests=total_requests,
                          fulfilled_requests=fulfilled_requests)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        name = request.form.get('name')
        phone = request.form.get('phone')
        role = request.form.get('role')
        city = request.form.get('city')
        district = request.form.get('district')
        blood_group = request.form.get('blood_group')
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
            return redirect(url_for('register'))
        
        user = User(
            email=email,
            name=name,
            phone=phone,
            role=role,
            city=city,
            district=district,
            blood_group=blood_group if role == 'donor' else None
        )
        user.set_password(password)
        
        # Organizations start unverified, individuals start verified
        user.is_verified = not user.is_organization()
        
        db.session.add(user)
        db.session.commit()
        
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        
        flash('Invalid email or password.', 'error')
    
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


# ============== DASHBOARD ==============

@app.route('/dashboard')
@login_required
def dashboard():
    # Get user's requests or responses based on role
    if current_user.is_organization():
        my_requests = EmergencyRequest.query.filter_by(requester_id=current_user.id)\
            .order_by(EmergencyRequest.created_at.desc()).limit(10).all()
        incoming_requests = []
    else:
        my_requests = []
        # Get requests matching donor's profile
        incoming_requests = get_matching_requests_for_user(current_user)
    
    # Recent activity
    recent_responses = RequestResponse.query.filter_by(responder_id=current_user.id)\
        .order_by(RequestResponse.notified_at.desc()).limit(5).all()
    
    # Network stats
    active_requests = EmergencyRequest.query.filter_by(status='open').count()
    
    return render_template('dashboard.html',
                          my_requests=my_requests,
                          incoming_requests=incoming_requests,
                          recent_responses=recent_responses,
                          active_requests=active_requests)


# ============== EMERGENCY REQUESTS ==============

@app.route('/request/new', methods=['GET', 'POST'])
@login_required
@org_required
def new_request():
    if request.method == 'POST':
        resource_type = request.form.get('resource_type')
        blood_group = request.form.get('blood_group')
        units_needed = int(request.form.get('units_needed', 1))
        urgency = request.form.get('urgency')
        hospital_name = request.form.get('hospital_name')
        patient_condition = request.form.get('patient_condition')
        
        # Check for duplicate requests
        existing = EmergencyRequest.query.filter_by(
            requester_id=current_user.id,
            resource_type=resource_type,
            blood_group=blood_group,
            status='open'
        ).first()
        
        if existing:
            flash('You already have an open request for this resource.', 'warning')
            return redirect(url_for('view_request', request_id=existing.id))
        
        emergency_request = EmergencyRequest(
            requester_id=current_user.id,
            resource_type=resource_type,
            blood_group=blood_group if resource_type in ['blood', 'plasma'] else None,
            units_needed=units_needed,
            urgency=urgency,
            city=current_user.city,
            district=current_user.district,
            hospital_name=hospital_name or current_user.name,
            patient_condition=patient_condition,
            expires_at=datetime.utcnow() + timedelta(hours=24 if urgency == 'normal' else 12)
        )
        
        db.session.add(emergency_request)
        db.session.commit()
        
        # Start matching process
        matches = find_matching_contributors(emergency_request)
        notify_contributors(emergency_request, matches)
        
        flash(f'Emergency request created. {len(matches)} potential contributors notified.', 'success')
        return redirect(url_for('view_request', request_id=emergency_request.id))
    
    return render_template('new_request.html')


@app.route('/request/<int:request_id>')
@login_required
def view_request(request_id):
    emergency_request = EmergencyRequest.query.get_or_404(request_id)
    responses = RequestResponse.query.filter_by(request_id=request_id).all()
    
    # Check if current user can respond
    can_respond = False
    user_response = None
    
    if not current_user.is_organization():
        user_response = RequestResponse.query.filter_by(
            request_id=request_id,
            responder_id=current_user.id
        ).first()
        
        if not user_response and emergency_request.status == 'open':
            can_respond = is_user_eligible_for_request(current_user, emergency_request)
    
    return render_template('view_request.html',
                          emergency_request=emergency_request,
                          responses=responses,
                          can_respond=can_respond,
                          user_response=user_response)


@app.route('/request/<int:request_id>/respond', methods=['POST'])
@login_required
def respond_to_request(request_id):
    emergency_request = EmergencyRequest.query.get_or_404(request_id)
    
    if emergency_request.status != 'open':
        flash('This request is no longer accepting responses.', 'error')
        return redirect(url_for('view_request', request_id=request_id))
    
    action = request.form.get('action')
    
    # Get or create response
    response = RequestResponse.query.filter_by(
        request_id=request_id,
        responder_id=current_user.id
    ).first()
    
    if not response:
        response = RequestResponse(
            request_id=request_id,
            responder_id=current_user.id
        )
        db.session.add(response)
    
    response.responded_at = datetime.utcnow()
    response_time = (response.responded_at - response.notified_at).total_seconds() / 60 if response.notified_at else 0
    
    if action == 'accept':
        response.status = 'accepted'
        response.units_offered = int(request.form.get('units_offered', 1))
        flash('Thank you! Your response has been recorded.', 'success')
        
        # Update request status
        emergency_request.status = 'matching'
        
    elif action == 'decline':
        response.status = 'declined'
        current_user.update_iri(fulfilled=False, response_time_minutes=response_time)
        flash('Response recorded.', 'info')
    
    db.session.commit()
    return redirect(url_for('view_request', request_id=request_id))


@app.route('/request/<int:request_id>/complete', methods=['POST'])
@login_required
@org_required
def complete_request(request_id):
    emergency_request = EmergencyRequest.query.get_or_404(request_id)
    
    if emergency_request.requester_id != current_user.id:
        flash('Unauthorized action.', 'error')
        return redirect(url_for('dashboard'))
    
    responder_id = request.form.get('responder_id')
    units_provided = int(request.form.get('units_provided', 1))
    rating = int(request.form.get('rating', 5))
    
    response = RequestResponse.query.filter_by(
        request_id=request_id,
        responder_id=responder_id
    ).first()
    
    if response:
        response.status = 'completed'
        response.completed_at = datetime.utcnow()
        response.units_provided = units_provided
        response.requester_rating = rating
        
        # Update responder's IRI
        responder = User.query.get(responder_id)
        response_time = (response.responded_at - response.notified_at).total_seconds() / 60 if response.notified_at and response.responded_at else 30
        responder.update_iri(fulfilled=True, response_time_minutes=response_time)
        
        # Update blood donor's last donation date
        if emergency_request.resource_type == 'blood' and responder.role == 'donor':
            responder.last_donation_date = datetime.utcnow().date()
        
        # Award ECC to requester organization
        ecc_earned = calculate_ecc(emergency_request, response)
        current_user.ecc_credits += ecc_earned
        
        # Log contribution
        log = ContributionLog(
            user_id=current_user.id,
            request_id=request_id,
            contribution_type='fulfillment',
            ecc_earned=ecc_earned,
            description=f'Fulfilled {emergency_request.resource_type} request'
        )
        db.session.add(log)
        
        # Update request status
        emergency_request.units_fulfilled += units_provided
        if emergency_request.units_fulfilled >= emergency_request.units_needed:
            emergency_request.status = 'fulfilled'
            emergency_request.fulfilled_at = datetime.utcnow()
            emergency_request.fulfilled_by_id = int(responder_id)
        else:
            emergency_request.status = 'partially_fulfilled'
        
        db.session.commit()
        flash('Request marked as completed. Thank you!', 'success')
    
    return redirect(url_for('view_request', request_id=request_id))


# ============== MATCHING ALGORITHM ==============

def find_matching_contributors(emergency_request):
    """Find eligible contributors sorted by reliability and proximity"""
    query = User.query.filter(
        User.is_available == True,
        User.id != emergency_request.requester_id
    )
    
    # Filter by resource type
    if emergency_request.resource_type == 'blood':
        query = query.filter(User.role == 'donor')
        # Filter by compatible blood groups
        if emergency_request.blood_group:
            compatible_donors = []
            for donor_group, can_donate_to in BLOOD_COMPATIBILITY.items():
                if emergency_request.blood_group in can_donate_to:
                    compatible_donors.append(donor_group)
            query = query.filter(User.blood_group.in_(compatible_donors))
    
    elif emergency_request.resource_type == 'ambulance':
        query = query.filter(User.role == 'ambulance')
    
    elif emergency_request.resource_type == 'volunteer':
        query = query.filter(User.role == 'volunteer')
    
    elif emergency_request.resource_type in ['plasma', 'oxygen']:
        query = query.filter(User.role.in_(['blood_bank', 'hospital', 'ngo']))
    
    # Filter by location (same city first)
    contributors = query.filter(User.city == emergency_request.city).all()
    
    # Expand search for rare blood groups or critical requests
    if len(contributors) < 3 or emergency_request.urgency == 'critical':
        if emergency_request.blood_group in RARE_BLOOD_GROUPS or emergency_request.urgency == 'critical':
            # Expand to district level
            district_contributors = query.filter(
                User.district == emergency_request.district,
                User.city != emergency_request.city
            ).all()
            contributors.extend(district_contributors)
            emergency_request.auto_expanded = True
    
    # Sort by eligibility and reliability (IRI/ECC as tie-breaker)
    def sort_key(user):
        # Primary: eligibility (blood donors must be eligible)
        eligible = 1 if user.role != 'donor' or user.can_donate_blood() else 0
        
        # Secondary: verification status
        verified = 1 if user.is_verified else 0
        
        # Tertiary: reliability score (IRI for individuals, ECC for orgs)
        reliability = user.iri_score if not user.is_organization() else min(100, user.ecc_credits)
        
        return (eligible, verified, reliability)
    
    contributors.sort(key=sort_key, reverse=True)
    
    # Limit to top candidates
    max_notifications = 10 if emergency_request.urgency == 'critical' else 5
    return contributors[:max_notifications]


def notify_contributors(emergency_request, contributors):
    """Create notification records for matched contributors (mocked for prototype)"""
    for user in contributors:
        response = RequestResponse(
            request_id=emergency_request.id,
            responder_id=user.id,
            status='notified',
            notified_at=datetime.utcnow()
        )
        db.session.add(response)
    
    db.session.commit()
    # In production: send SMS/email/push notifications


def is_user_eligible_for_request(user, emergency_request):
    """Check if a user is eligible to respond to a request"""
    if not user.is_available:
        return False
    
    if emergency_request.resource_type == 'blood':
        if user.role != 'donor':
            return False
        if not user.can_donate_blood():
            return False
        # Check blood compatibility
        if emergency_request.blood_group:
            can_donate_to = BLOOD_COMPATIBILITY.get(user.blood_group, [])
            if emergency_request.blood_group not in can_donate_to:
                return False
    
    return True


def get_matching_requests_for_user(user):
    """Get open requests that match a user's profile"""
    query = EmergencyRequest.query.filter(
        EmergencyRequest.status == 'open',
        EmergencyRequest.city == user.city
    )
    
    if user.role == 'donor' and user.blood_group:
        # Filter by blood compatibility
        can_donate_to = BLOOD_COMPATIBILITY.get(user.blood_group, [])
        query = query.filter(
            EmergencyRequest.resource_type == 'blood',
            EmergencyRequest.blood_group.in_(can_donate_to)
        )
    elif user.role == 'volunteer':
        query = query.filter(EmergencyRequest.resource_type == 'volunteer')
    elif user.role == 'ambulance':
        query = query.filter(EmergencyRequest.resource_type == 'ambulance')
    
    return query.order_by(
        EmergencyRequest.urgency.desc(),
        EmergencyRequest.created_at.desc()
    ).limit(10).all()


def calculate_ecc(emergency_request, response):
    """Calculate Emergency Contribution Credits earned"""
    base_ecc = 10
    
    # Urgency multiplier
    urgency_multiplier = {
        'critical': 3,
        'urgent': 2,
        'normal': 1
    }
    
    # Rare resource bonus
    rare_bonus = 5 if emergency_request.blood_group in RARE_BLOOD_GROUPS else 0
    
    # Rating bonus
    rating_bonus = (response.requester_rating or 3) - 3  # -2 to +2
    
    return int(base_ecc * urgency_multiplier.get(emergency_request.urgency, 1) + rare_bonus + rating_bonus)


# ============== PROFILE & SETTINGS ==============

@app.route('/profile')
@login_required
def profile():
    contributions = ContributionLog.query.filter_by(user_id=current_user.id)\
        .order_by(ContributionLog.created_at.desc()).limit(10).all()
    
    return render_template('profile.html', contributions=contributions)


@app.route('/profile/availability', methods=['POST'])
@login_required
def toggle_availability():
    current_user.is_available = not current_user.is_available
    db.session.commit()
    
    status = 'available' if current_user.is_available else 'unavailable'
    flash(f'You are now marked as {status}.', 'success')
    return redirect(url_for('profile'))


@app.route('/profile/update', methods=['POST'])
@login_required
def update_profile():
    current_user.phone = request.form.get('phone', current_user.phone)
    current_user.city = request.form.get('city', current_user.city)
    current_user.district = request.form.get('district', current_user.district)
    current_user.address = request.form.get('address', current_user.address)
    
    if current_user.role == 'donor':
        current_user.blood_group = request.form.get('blood_group', current_user.blood_group)
    
    db.session.commit()
    flash('Profile updated successfully.', 'success')
    return redirect(url_for('profile'))


# ============== NETWORK & LEADERBOARD ==============

@app.route('/network')
@login_required
def network():
    # Top organizations by ECC
    top_orgs = User.query.filter(
        User.role.in_(['hospital', 'blood_bank', 'ngo', 'ambulance']),
        User.is_verified == True
    ).order_by(User.ecc_credits.desc()).limit(10).all()
    
    # Top contributors by IRI
    top_contributors = User.query.filter(
        User.role.in_(['donor', 'volunteer'])
    ).order_by(User.iri_score.desc()).limit(10).all()
    
    # Network statistics
    stats = {
        'total_organizations': User.query.filter(User.role.in_(['hospital', 'blood_bank', 'ngo', 'ambulance'])).count(),
        'total_donors': User.query.filter_by(role='donor').count(),
        'total_volunteers': User.query.filter_by(role='volunteer').count(),
        'requests_fulfilled': EmergencyRequest.query.filter_by(status='fulfilled').count(),
        'active_requests': EmergencyRequest.query.filter_by(status='open').count()
    }
    
    return render_template('network.html',
                          top_orgs=top_orgs,
                          top_contributors=top_contributors,
                          stats=stats)


@app.route('/requests')
@login_required
def all_requests():
    status_filter = request.args.get('status', 'open')
    resource_filter = request.args.get('resource')
    urgency_filter = request.args.get('urgency')
    
    query = EmergencyRequest.query
    
    if status_filter:
        query = query.filter_by(status=status_filter)
    if resource_filter:
        query = query.filter_by(resource_type=resource_filter)
    if urgency_filter:
        query = query.filter_by(urgency=urgency_filter)
    
    requests_list = query.order_by(EmergencyRequest.created_at.desc()).limit(50).all()
    
    return render_template('requests.html', requests=requests_list)


# ============== API ENDPOINTS ==============

@app.route('/api/availability', methods=['POST'])
@login_required
def api_toggle_availability():
    current_user.is_available = not current_user.is_available
    db.session.commit()
    return jsonify({'available': current_user.is_available})


@app.route('/api/requests/nearby')
@login_required
def api_nearby_requests():
    requests = get_matching_requests_for_user(current_user)
    return jsonify([{
        'id': r.id,
        'resource_type': r.resource_type,
        'blood_group': r.blood_group,
        'urgency': r.urgency,
        'hospital_name': r.hospital_name,
        'city': r.city,
        'created_at': r.created_at.isoformat()
    } for r in requests])


# ============== INITIALIZATION ==============

def init_db():
    """Initialize database with sample data"""
    with app.app_context():
        db.create_all()
        
        # Check if data already exists
        if User.query.first():
            return
        
        # Create sample hospital
        hospital = User(
            email='hospital@example.com',
            name='City General Hospital',
            phone='1234567890',
            role='hospital',
            city='Mumbai',
            district='Mumbai Suburban',
            is_verified=True,
            ecc_credits=50
        )
        hospital.set_password('password123')
        db.session.add(hospital)
        
        # Create sample blood bank
        blood_bank = User(
            email='bloodbank@example.com',
            name='Red Cross Blood Bank',
            phone='0987654321',
            role='blood_bank',
            city='Mumbai',
            district='Mumbai Suburban',
            is_verified=True,
            ecc_credits=100
        )
        blood_bank.set_password('password123')
        db.session.add(blood_bank)
        
        # Create sample donors
        blood_groups = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']
        for i, bg in enumerate(blood_groups):
            donor = User(
                email=f'donor{i+1}@example.com',
                name=f'Donor {bg}',
                phone=f'555000{i+1:04d}',
                role='donor',
                city='Mumbai',
                district='Mumbai Suburban',
                blood_group=bg,
                is_verified=True,
                iri_score=50 + (i * 5)
            )
            donor.set_password('password123')
            db.session.add(donor)
        
        # Create sample volunteer
        volunteer = User(
            email='volunteer@example.com',
            name='Community Volunteer',
            phone='5551234567',
            role='volunteer',
            city='Mumbai',
            district='Mumbai Suburban',
            is_verified=True,
            iri_score=75
        )
        volunteer.set_password('password123')
        db.session.add(volunteer)
        
        # Create sample ambulance service
        ambulance = User(
            email='ambulance@example.com',
            name='Quick Response Ambulance',
            phone='108',
            role='ambulance',
            city='Mumbai',
            district='Mumbai Suburban',
            is_verified=True,
            ecc_credits=75
        )
        ambulance.set_password('password123')
        db.session.add(ambulance)
        
        db.session.commit()
        print("Database initialized with sample data!")


if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)
