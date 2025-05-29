from flask import Flask, render_template, request, redirect, url_for, flash, session
from datetime import datetime, date, timedelta
import calendar
import secrets # Added for CSRF token generation
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'  # Change this in a real application!
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'  # Redirect to login page if user is not authenticated
bcrypt = Bcrypt(app)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(60), nullable=False)
    height = db.Column(db.Float, nullable=True)
    weight = db.Column(db.Float, nullable=True)
    age = db.Column(db.Integer, nullable=True)
    gender = db.Column(db.String(10), nullable=True)
    activity_level = db.Column(db.Float, nullable=True)
    bmr = db.Column(db.Float, nullable=True)
    target_date = db.Column(db.Date, nullable=True)
    daily_calorie_goal = db.Column(db.Integer, nullable=True)
    entries = db.relationship('CalorieEntry', backref='user', lazy=True)
    last_reset_date = db.Column(db.Date, nullable=True)
    exercise_tracking_mode = db.Column(db.String(10), default='separate') # 'separate' or 'offset'
    exercise_entries = db.relationship('ExerciseEntry', backref='user', lazy=True)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        user = User(email=email, password_hash=hashed_password, last_reset_date=date.today())
        db.session.add(user)
        db.session.commit()
        flash('Your account has been created! You are now able to log in', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user)
            session['_csrf_token'] = secrets.token_hex(16) # Generate and store CSRF token
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Login Unsuccessful. Please check email and password', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@app.route('/dashboard/', defaults={'year': None, 'month': None})
@app.route('/dashboard/<int:year>/<int:month>')
@login_required
def dashboard(year, month):
    user = User.query.get(current_user.id)
    weight_entries_db = WeightEntry.query.filter_by(user_id=current_user.id).order_by(WeightEntry.date.asc()).all()
    weight_entries_serializable = [
        {'date': entry.date.strftime('%Y-%m-%d'), 'weight': entry.weight}
        for entry in weight_entries_db
    ]
    
    current_dt = date.today()
    if year is None:
        year = current_dt.year
    if month is None:
        month = current_dt.month
    
    try:
        # Ensure year and month are integers if they came from the URL
        year = int(year)
        month = int(month)
        if not (1 <= month <= 12 and 1900 <= year <= 2100):
             raise ValueError("Year or month out of valid range")
        display_date = date(year, month, 1) # Used for prev/next month logic
    except (ValueError, TypeError):
        # Redirect to current month dashboard if year/month are invalid from URL
        return redirect(url_for('dashboard', year=current_dt.year, month=current_dt.month))

    cal = calendar.Calendar()
    # monthdatescalendar gives datetime.date objects
    month_dates_calendar = cal.monthdatescalendar(year, month)
    
    calendar_data = []

    for week_of_dates in month_dates_calendar:
        week_data = []
        for day_date_obj in week_of_dates: # day_date_obj is a datetime.date
            target_for_day = None
            consumed_for_day = 0

            # Determine if the day is within an active goal period
            is_within_goal_period = False
            if user.daily_calorie_goal and user.target_date:
                # A simple assumption: goal starts from the first day of the month of target_date
                # This is a simplification. A proper goal_start_date field in User model would be better.
                # For now, we assume the goal is active for any day up to and including target_date
                # and for the current implementation, the target is applied if day_date_obj is in current display month.
                if day_date_obj <= user.target_date:
                    is_within_goal_period = True
            
            if is_within_goal_period and day_date_obj.month == month:
                target_for_day = user.daily_calorie_goal

            # Fetch CalorieEntry for the day
            entry = CalorieEntry.query.filter_by(user_id=user.id, date=day_date_obj).first()
            adjusted_for_day = 0

            calorie_entry_id_for_day = None # Initialize

            if entry:
                consumed_for_day = entry.consumed_calories
                adjusted_for_day = entry.adjusted_calories
                calorie_entry_id_for_day = entry.id # Store CalorieEntry ID
                # If entry has its own target, it overrides the goal's general target for that day
                if entry.target_calories_for_day is not None:
                    target_for_day = entry.target_calories_for_day
                # If entry exists but target_for_day is still None (e.g. created outside goal),
                # but this day IS in a goal period, set it.
                elif is_within_goal_period and day_date_obj.month == month:
                     # We don't persist this back to entry from here, record_calories will handle it if visited
                     pass # target_for_day is already user.daily_calorie_goal from above

            effective_consumed = consumed_for_day + adjusted_for_day

            # This is the day_info dictionary used for fc_events generation later
            week_data.append({
                'date': day_date_obj, 
                'day_number': day_date_obj.day,
                'is_current_month': day_date_obj.month == month,
                'consumed_calories': consumed_for_day, 
                'adjusted_calories': adjusted_for_day, 
                'effective_consumed': effective_consumed, 
                'target_calories_for_day': target_for_day,
                'entry_id': calorie_entry_id_for_day # Added entry_id
            })
        calendar_data.append(week_data)
            
    # For prev/next month links:
    # display_date is the 1st of the current (year, month)
    prev_month_display = display_date - timedelta(days=1) # Takes to last day of prev month
    next_month_display = display_date + timedelta(days=32) # Takes to somewhere in next month

    prev_year = prev_month_display.year
    prev_month = prev_month_display.month
    next_year = next_month_display.year
    next_month = next_month_display.month

    # Cycle Progress & Exercise Totals
    total_deficit_since_last_reset = calculate_total_progress(user.id, user.last_reset_date)
    cycle_complete_flag = session.get('cycle_complete', False)

    if not cycle_complete_flag and total_deficit_since_last_reset >= 7200:
        session['cycle_complete'] = True
        cycle_complete_flag = True # Update for current render

    total_exercise_kcal_since_reset = 0
    if user.exercise_tracking_mode == 'separate':
        exercise_query = ExerciseEntry.query.filter_by(user_id=user.id)
        if user.last_reset_date:
            exercise_query = exercise_query.filter(ExerciseEntry.date > user.last_reset_date)
        all_exercise_entries_for_period = exercise_query.all()
        total_exercise_kcal_since_reset = sum(ex.calories_burned for ex in all_exercise_entries_for_period)
    
    # Augment calendar_data with daily exercise
    # Fetch all exercise entries for the displayed month for efficiency
    first_day_of_month = date(year, month, 1)
    # To get last day, go to first of next month and subtract one day
    if month == 12:
        last_day_of_month = date(year, month, 31) # Approx, or use calendar.monthrange
    else:
        last_day_of_month = date(year, month + 1, 1) - timedelta(days=1)

    monthly_exercise_entries = ExerciseEntry.query.filter(
        ExerciseEntry.user_id == user.id,
        ExerciseEntry.date >= first_day_of_month,
        ExerciseEntry.date <= last_day_of_month
    ).all()
    
    daily_exercise_map = {entry.date: entry.calories_burned for entry in monthly_exercise_entries}

    for week_data in calendar_data:
        for day_info in week_data:
            day_info['daily_exercise_calories'] = daily_exercise_map.get(day_info['date'], 0)
            # If offset mode, the target should visually increase or consumed effectively decrease
            if user.exercise_tracking_mode == 'offset' and day_info['target_calories_for_day'] is not None:
                # The calculate_total_progress already handles the math for deficit.
                # For display, we can show an "effective target" or adjust "effective consumed"
                # Let's adjust effective_consumed to show food - exercise vs target
                 day_info['effective_consumed'] -= day_info['daily_exercise_calories']


    # Prepare events for FullCalendar
    calendar_events = []
    for week_data in calendar_data:
        for day_info in week_data:
            if not day_info['is_current_month']: # Only show events for the current month in view
                continue

            event_title_parts = []
            class_names = ['fc-event-day-cell'] # Base class for all day cells with data

            if day_info['target_calories_for_day'] is not None:
                event_title_parts.append(f"{day_info['effective_consumed']} / {day_info['target_calories_for_day']} kcal")
                if day_info['effective_consumed'] > day_info['target_calories_for_day']:
                    class_names.append('fc-event-over')
                elif day_info['effective_consumed'] < day_info['target_calories_for_day']:
                    class_names.append('fc-event-under')
                else:
                    class_names.append('fc-event-target-met')
            else:
                event_title_parts.append(f"{day_info['effective_consumed']} kcal (No target)")
                class_names.append('fc-event-no-target')
            
            if day_info['daily_exercise_calories'] > 0:
                event_title_parts.append(f"+{day_info['daily_exercise_calories']} ex")
                class_names.append('fc-event-exercise')
            
            if day_info['adjusted_calories'] > 0:
                event_title_parts.append(f"({day_info['adjusted_calories']} adj in)")
            elif day_info['adjusted_calories'] < 0: # Should not happen with current logic, but for future
                event_title_parts.append(f"({abs(day_info['adjusted_calories'])} adj out)")


            calendar_events.append({
                'title': '\n'.join(event_title_parts), # Use newline to separate info lines
                'start': day_info['date'].strftime('%Y-%m-%d'),
                'allDay': True,
                'display': 'block', # Ensures text is visible and background is not the event itself
                'classNames': class_names,
                'extendedProps': { 
                    'calorie_entry_id': day_info.get('entry_id'), # Get the CalorieEntry ID
                    'original_date': day_info['date'].strftime('%Y-%m-%d'), # Date of the cell
                    'current_adjusted_calories': day_info.get('adjusted_calories', 0),
                    'target_calories': day_info.get('target_calories_for_day'), # Can be None
                    'consumed_calories_raw': day_info.get('consumed_calories', 0),
                    # Retain other potentially useful props if needed
                    'raw_exercise': day_info['daily_exercise_calories'] 
                }
            })

    # Data for Daily Achievement Pie Chart (latest day with target)
    daily_pie_chart_data = None
    today = date.today()
    # Check last 7 days for an entry with a target
    for i in range(7):
        current_check_date = today - timedelta(days=i)
        calorie_entry = CalorieEntry.query.filter_by(user_id=user.id, date=current_check_date).first()
        
        if calorie_entry and calorie_entry.target_calories_for_day is not None:
            effective_consumed = calorie_entry.consumed_calories + calorie_entry.adjusted_calories
            target_kcal = calorie_entry.target_calories_for_day

            # Apply exercise offset if applicable
            if user.exercise_tracking_mode == 'offset':
                exercise_on_day = ExerciseEntry.query.filter_by(user_id=user.id, date=current_check_date).all()
                total_exercise_kcal_on_day = sum(ex.calories_burned for ex in exercise_on_day)
                effective_consumed -= total_exercise_kcal_on_day
            
            consumed_part = max(0, effective_consumed) # Ensure consumed is not negative after exercise offset for pie chart logic
            
            labels = []
            values = []
            
            if consumed_part <= target_kcal:
                labels.append('Consumed')
                values.append(consumed_part)
                if consumed_part < target_kcal : # Only add "Remaining" if there's actually something remaining
                    labels.append('Remaining')
                    values.append(target_kcal - consumed_part)
                elif consumed_part == target_kcal: # If exactly on target, "Remaining" is 0, can omit or show. Let's omit.
                    pass 
            else: # Over target
                labels.append('Target Met') # Portion that met the target
                values.append(target_kcal)
                labels.append('Over Consumed') # Portion over the target
                values.append(consumed_part - target_kcal)

            if values: # Only set data if there's something to show
                daily_pie_chart_data = {
                    'labels': labels,
                    'values': values,
                    'date_str': current_check_date.strftime('%Y-%m-%d'),
                    'target_kcal': target_kcal,
                    'consumed_kcal': consumed_part # This is effective consumed
                }
            break # Found the latest day with data and target

    # Data for Cumulative Progress Line Chart
    cumulative_chart_data = None
    if user.last_reset_date:
        start_date = user.last_reset_date
        end_date = date.today()
        delta = end_date - start_date
        
        if delta.days >= 1: # Need at least two data points (start and one more day) for a line
            date_labels = []
            cumulative_target_kcal_list = []
            cumulative_consumed_kcal_list = []
            cumulative_exercise_kcal_list = []

            current_cumulative_target = 0
            current_cumulative_consumed = 0
            current_cumulative_exercise = 0

            for i in range(delta.days + 1):
                current_day = start_date + timedelta(days=i)
                date_labels.append(current_day.strftime('%Y-%m-%d'))

                daily_target = 0
                daily_consumed_effective = 0
                daily_exercise = 0

                calorie_entry = CalorieEntry.query.filter_by(user_id=user.id, date=current_day).first()
                if calorie_entry:
                    if calorie_entry.target_calories_for_day is not None:
                        daily_target = calorie_entry.target_calories_for_day
                    daily_consumed_effective = calorie_entry.consumed_calories + calorie_entry.adjusted_calories
                
                exercise_entries_on_day = ExerciseEntry.query.filter_by(user_id=user.id, date=current_day).all()
                daily_exercise = sum(ex.calories_burned for ex in exercise_entries_on_day)

                current_cumulative_target += daily_target
                current_cumulative_consumed += daily_consumed_effective
                current_cumulative_exercise += daily_exercise
                
                cumulative_target_kcal_list.append(current_cumulative_target)
                cumulative_consumed_kcal_list.append(current_cumulative_consumed)
                cumulative_exercise_kcal_list.append(current_cumulative_exercise)
            
            cumulative_chart_data = {
                'labels': date_labels,
                'datasets': [
                    {
                        'label': 'Cumulative Target Calories',
                        'data': cumulative_target_kcal_list,
                        'borderColor': 'rgba(54, 162, 235, 1)', # Blue
                        'backgroundColor': 'rgba(54, 162, 235, 0.5)',
                        'fill': False,
                        'tension': 0.1
                    },
                    {
                        'label': 'Cumulative Consumed Calories (Net)', # Net of food intake + adjustments
                        'data': cumulative_consumed_kcal_list,
                        'borderColor': 'rgba(255, 99, 132, 1)', # Red
                        'backgroundColor': 'rgba(255, 99, 132, 0.5)',
                        'fill': False,
                        'tension': 0.1
                    },
                    {
                        'label': 'Cumulative Exercise Calories Burned',
                        'data': cumulative_exercise_kcal_list,
                        'borderColor': 'rgba(75, 192, 192, 1)', # Green
                        'backgroundColor': 'rgba(75, 192, 192, 0.5)',
                        'fill': False,
                        'tension': 0.1
                    }
                ]
            }
            # If only one data point after processing, it's not enough for a line graph.
            if len(date_labels) < 2 :
                 cumulative_chart_data = None


    csrf_token_for_template = session.get('_csrf_token') # Get CSRF token for template

    return render_template('dashboard.html', user=user,
                           fc_events=calendar_events, 
                           cal_year=year, cal_month=month,
                           prev_year=prev_year, prev_month=prev_month,
                           next_year=next_year, next_month=next_month,
                           total_deficit_since_last_reset=total_deficit_since_last_reset,
                           cycle_complete_flag=cycle_complete_flag,
                           total_exercise_kcal_since_reset=total_exercise_kcal_since_reset,
                           daily_pie_chart_data=daily_pie_chart_data,
                           cumulative_chart_data=cumulative_chart_data,
                           csrf_token_for_js=csrf_token_for_template,
                           weight_entries=weight_entries_serializable) # Pass CSRF token to template


@app.route('/cycle_reset', methods=['GET', 'POST'])
@login_required
def cycle_reset():
    if not session.get('cycle_complete'):
        flash('Cycle not yet complete or already reset.', 'info')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        try:
            new_weight = float(request.form.get('new_weight'))
            if new_weight <= 0:
                flash('Please enter a valid weight.', 'danger')
                return render_template('cycle_reset.html')
        except (ValueError, TypeError):
            flash('Invalid input for weight.', 'danger')
            return render_template('cycle_reset.html')

        current_user.weight = new_weight
        current_user.last_reset_date = date.today()
        current_user.target_date = None # Reset goal
        current_user.daily_calorie_goal = None # Reset goal
        
        session.pop('cycle_complete', None)
        db.session.commit()
        
        flash('Weight updated successfully! Please review your BMR and set a new goal.', 'success')
        return redirect(url_for('initial_setup')) # Guide to initial_setup then goal

    return render_template('cycle_reset.html')

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        new_mode = request.form.get('exercise_tracking_mode')
        if new_mode in ['separate', 'offset']:
            current_user.exercise_tracking_mode = new_mode
            db.session.commit()
            flash(f'Exercise tracking mode updated to "{new_mode}".', 'success')
        else:
            flash('Invalid tracking mode selected.', 'danger')
        return redirect(url_for('settings'))
    
    return render_template('settings.html', current_mode=current_user.exercise_tracking_mode)

@app.route('/record_exercise', methods=['GET', 'POST'])
@login_required
def record_exercise():
    if request.method == 'POST':
        try:
            date_str = request.form.get('date')
            calories_burned = int(request.form.get('calories_burned'))
            description = request.form.get('description', '')
            
            if not date_str:
                flash('Date is required.', 'danger')
                return render_template('record_exercise.html', today_date=date.today())

            exercise_date = datetime.strptime(date_str, '%Y-%m-%d').date()

            if calories_burned <= 0:
                flash('Calories burned must be a positive number.', 'danger')
                return render_template('record_exercise.html', today_date=date.today(), date_val=date_str, cal_val=calories_burned, desc_val=description)

            new_exercise = ExerciseEntry(
                date=exercise_date,
                user_id=current_user.id,
                calories_burned=calories_burned,
                description=description
            )
            db.session.add(new_exercise)
            db.session.commit()
            flash(f'Exercise for {exercise_date.strftime("%Y-%m-%d")} recorded successfully!', 'success')
            return redirect(url_for('dashboard', year=exercise_date.year, month=exercise_date.month))
        
        except ValueError:
            flash('Invalid input for calories burned. Please enter a number.', 'danger')
            return render_template('record_exercise.html', today_date=date.today(), date_val=request.form.get('date'), cal_val=request.form.get('calories_burned'), desc_val=request.form.get('description'))
        except Exception as e:
            flash(f'An error occurred: {str(e)}', 'danger')
            return render_template('record_exercise.html', today_date=date.today(), date_val=request.form.get('date'), cal_val=request.form.get('calories_burned'), desc_val=request.form.get('description'))

    return render_template('record_exercise.html', today_date=date.today())

@app.route('/api/update_calorie_adjustment', methods=['POST'])
@login_required
def api_update_calorie_adjustment():
    # CSRF Token Validation
    submitted_csrf_token = request.headers.get('X-CSRF-Token')
    session_csrf_token = session.get('_csrf_token')

    if not session_csrf_token or not submitted_csrf_token:
        return jsonify({'status': 'error', 'message': 'CSRF token missing.'}), 403
    if not secrets.compare_digest(session_csrf_token, submitted_csrf_token): # Use compare_digest for security
        return jsonify({'status': 'error', 'message': 'CSRF token invalid.'}), 403

    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'Invalid JSON payload'}), 400

    original_date_str = data.get('original_date_str')
    new_date_str = data.get('new_date_str')
    calorie_entry_id = data.get('calorie_entry_id') # This ID refers to the CalorieEntry on the original_date
    adjustment_amount_to_move = data.get('adjustment_amount') # The amount of adj_cal to move

    if not all([original_date_str, new_date_str, calorie_entry_id is not None, adjustment_amount_to_move is not None]):
        return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400

    try:
        original_date = datetime.strptime(original_date_str, '%Y-%m-%d').date()
        new_date = datetime.strptime(new_date_str, '%Y-%m-%d').date()
        adjustment_amount_to_move = int(adjustment_amount_to_move)
    except (ValueError, TypeError) as e:
        return jsonify({'status': 'error', 'message': f'Invalid data format: {str(e)}'}), 400

    try:
        # 1. Process Source Entry
        source_entry = CalorieEntry.query.filter_by(id=calorie_entry_id, user_id=current_user.id).first()

        if not source_entry:
            return jsonify({'status': 'error', 'message': 'Source calorie entry not found or not owned by user.'}), 404
        
        if source_entry.date != original_date:
            # This implies the event dragged might not be the one with the ID, or date mismatch.
            # This can happen if a day cell has no entry, but we still need to move 'potential' adjustments
            # For this subtask, we assume calorie_entry_id IS the one from original_date.
            return jsonify({'status': 'error', 
                            'message': f'Source entry date mismatch. Expected {original_date_str}, got {source_entry.date.strftime("%Y-%m-%d")}.'}), 400
        
        # Subtract the amount that is being moved from the source entry's adjusted_calories
        source_entry.adjusted_calories -= adjustment_amount_to_move
        
        # 2. Process Target Entry
        target_entry = CalorieEntry.query.filter_by(date=new_date, user_id=current_user.id).first()

        if target_entry:
            target_entry.adjusted_calories += adjustment_amount_to_move
        else:
            # Determine target_calories_for_day for the new entry
            # This logic should mirror how targets are usually determined (e.g., from user's goal if active)
            target_for_new_day = None
            if current_user.daily_calorie_goal and current_user.target_date and new_date <= current_user.target_date:
                 # A simplified check: if a general goal is active and new_date is within its period
                 # More precise logic might be needed if goals are date-specific beyond just target_date
                 target_for_new_day = current_user.daily_calorie_goal

            target_entry = CalorieEntry(
                date=new_date,
                user_id=current_user.id,
                consumed_calories=0, # Dragging adjustment does not move consumed calories
                adjusted_calories=adjustment_amount_to_move,
                target_calories_for_day=target_for_new_day 
            )
            db.session.add(target_entry)
            
        db.session.commit()
        return jsonify({'status': 'success', 'message': 'Calorie adjustment successful'}), 200

    except Exception as e:
        db.session.rollback()
        # Log the exception e for server-side debugging
        app.logger.error(f"Error in /api/update_calorie_adjustment: {str(e)}")
        return jsonify({'status': 'error', 'message': f'An internal error occurred: {str(e)}'}), 500


@app.route('/record_calories/<date_str>', methods=['GET', 'POST'])
@login_required
def record_calories(date_str):
    try:
        entry_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        flash('Invalid date format.', 'danger')
        return redirect(url_for('dashboard'))

    user = User.query.get(current_user.id)
    calorie_entry = CalorieEntry.query.filter_by(user_id=user.id, date=entry_date).first()

    target_calories_for_day = None
    if user.daily_calorie_goal and user.target_date and entry_date <= user.target_date:
        # Assuming goal starts from the first day of the month of target_date or some other logic
        # For now, if date is on or before target_date and a goal is set, apply daily_calorie_goal
        # A more precise goal_start_date would be better.
        # Let's consider the goal active if entry_date is not in the future relative to today,
        # and before or on the target_date.
        # This logic should ideally mirror what's in dashboard for consistency.
        if entry_date <= user.target_date: # Simplified condition
             target_calories_for_day = user.daily_calorie_goal

    if request.method == 'POST':
        try:
            consumed = request.form.get('consumed_calories')
            if consumed is None or consumed.strip() == '': # Handle empty string
                consumed_calories = 0
            else:
                consumed_calories = int(consumed)
            
            if consumed_calories < 0:
                flash('Consumed calories cannot be negative.', 'danger')
                # Return render_template to show the form again with the error and existing values
                return render_template('record_calories.html',
                                       date_str=date_str,
                                       entry_date=entry_date,
                                       consumed_calories_val=calorie_entry.consumed_calories if calorie_entry else 0,
                                       target_calories_for_day=target_calories_for_day,
                                       error="Consumed calories cannot be negative.")

        except ValueError:
            flash('Invalid input for calories. Please enter a number.', 'danger')
            # Return render_template to show the form again
            return render_template('record_calories.html',
                                   date_str=date_str,
                                   entry_date=entry_date,
                                   consumed_calories_val=calorie_entry.consumed_calories if calorie_entry else 0,
                                   target_calories_for_day=target_calories_for_day,
                                   error="Invalid input. Must be a number.")

        if calorie_entry:
            calorie_entry.consumed_calories = consumed_calories
            if calorie_entry.target_calories_for_day is None and target_calories_for_day is not None:
                 calorie_entry.target_calories_for_day = target_calories_for_day
        else:
            calorie_entry = CalorieEntry(
                date=entry_date,
                user_id=user.id,
                consumed_calories=consumed_calories,
                target_calories_for_day=target_calories_for_day
            )
            db.session.add(calorie_entry)
        db.session.commit() # Commit to get calorie_entry.id if new, and save changes

        # Check for overflow
        effective_consumed = calorie_entry.consumed_calories + calorie_entry.adjusted_calories
        if calorie_entry.target_calories_for_day is not None and effective_consumed > calorie_entry.target_calories_for_day:
            overflow_amount = effective_consumed - calorie_entry.target_calories_for_day
            session['pending_overflow'] = {
                'date_str': entry_date.strftime('%Y-%m-%d'),
                'amount': overflow_amount,
                'entry_id': calorie_entry.id # Store entry_id to easily retrieve/update later
            }
            flash(f'You have an overflow of {overflow_amount} calories for {entry_date.strftime("%Y-%m-%d")}. Please choose how to handle it.', 'warning')
            return redirect(url_for('handle_overflow'))
        
        flash(f'Calories for {entry_date.strftime("%Y-%m-%d")} updated successfully!', 'success')
        return redirect(url_for('dashboard', year=entry_date.year, month=entry_date.month))

    # GET request or if POST had an error and re-renders
    consumed_val = 0
    if calorie_entry:
        consumed_val = calorie_entry.consumed_calories
    
    # If a calorie_entry exists, its target might be more specific or already set.
    # If not, and we determined a target_calories_for_day from an active goal, use that.
    display_target = calorie_entry.target_calories_for_day if calorie_entry and calorie_entry.target_calories_for_day is not None else target_calories_for_day
    
    return render_template('record_calories.html', 
                           date_str=date_str, 
                           entry_date=entry_date, 
                           consumed_calories_val=consumed_val, 
                           target_calories_for_day=display_target)

@app.route('/handle_overflow', methods=['GET', 'POST'])
@login_required
def handle_overflow():
    if 'pending_overflow' not in session:
        return redirect(url_for('dashboard'))

    overflow_data = session['pending_overflow']
    overflow_amount = overflow_data['amount']
    overflow_date_str = overflow_data['date_str']
    overflow_date = datetime.strptime(overflow_date_str, '%Y-%m-%d').date()
    
    user = User.query.get(current_user.id)
    if not user.target_date: # Should not happen if overflow was triggered from a day with target
        flash('Target date not set, cannot handle overflow distribution.', 'danger')
        session.pop('pending_overflow', None)
        return redirect(url_for('dashboard'))

    remaining_days = 0
    if user.target_date > overflow_date:
        remaining_days = (user.target_date - overflow_date).days -1 # -1 because we start from next day

    if request.method == 'POST':
        mode = request.form.get('mode')
        original_entry = CalorieEntry.query.get(overflow_data['entry_id'])

        if not original_entry:
            flash('Error retrieving original calorie entry.', 'danger')
            session.pop('pending_overflow', None)
            return redirect(url_for('dashboard'))

        if mode == 'mode_A_carry_next_day':
            next_day_date = overflow_date + timedelta(days=1)
            next_day_entry = CalorieEntry.query.filter_by(user_id=user.id, date=next_day_date).first()
            if not next_day_entry:
                next_day_entry = CalorieEntry(user_id=user.id, date=next_day_date, consumed_calories=0)
                db.session.add(next_day_entry)
            
            next_day_entry.adjusted_calories = next_day_entry.adjusted_calories + overflow_amount
            # We are adding to adjusted_calories, so the original entry's overflow is now "handled"
            # by reducing its consumed part that caused the overflow, effectively moving it.
            # This means the original day is now "at target".
            original_entry.consumed_calories = original_entry.consumed_calories - overflow_amount
            # OR, simpler: original_entry.adjusted_calories -= overflow_amount to balance it out
            # Let's stick to moving the *actual consumed* part by adjusting the original entry
            # This implies the "overflow_amount" was never truly part of that day's *final* record.
            # However, the task implies the original day *did* have that consumption, and we are compensating *later*.
            # So, the original entry should remain as recorded, and `adjusted_calories` on future days
            # will be positive to signify an *additional burden* from a past overflow.
            # The `pending_overflow` amount is the *excess*. This excess needs to be added to `adjusted_calories` of future days.

            db.session.commit()
            flash(f'{overflow_amount} calories carried over to {next_day_date.strftime("%Y-%m-%d")}.', 'success')

        elif mode == 'mode_B_distribute_remaining':
            if remaining_days <= 0:
                flash('No remaining days to distribute. Please carry over to next day or adjust goal.', 'warning')
                return redirect(url_for('handle_overflow')) # Show page again

            daily_extra_to_add = round(overflow_amount / remaining_days) # Simple rounding
            if daily_extra_to_add == 0 and overflow_amount > 0 : daily_extra_to_add = 1 # Ensure at least 1 if overflow

            for i in range(remaining_days):
                current_dist_date = overflow_date + timedelta(days=i + 1)
                if current_dist_date > user.target_date: break # Should not happen if remaining_days is correct

                day_entry = CalorieEntry.query.filter_by(user_id=user.id, date=current_dist_date).first()
                if not day_entry:
                    day_entry = CalorieEntry(user_id=user.id, date=current_dist_date, consumed_calories=0)
                    db.session.add(day_entry)
                day_entry.adjusted_calories = day_entry.adjusted_calories + daily_extra_to_add
            
            db.session.commit()
            flash(f'{overflow_amount} calories distributed over {remaining_days} days.', 'success')
        
        session.pop('pending_overflow', None)
        return redirect(url_for('dashboard', year=overflow_date.year, month=overflow_date.month))

    return render_template('handle_overflow.html', 
                           overflow_amount=overflow_amount, 
                           overflow_date_str=overflow_date_str,
                           remaining_days=remaining_days)

@app.route('/record_weight', methods=['GET', 'POST'])
@login_required
def record_weight():
    if request.method == 'POST':
        date_str = request.form.get('date')
        weight_str = request.form.get('weight')

        if not date_str:
            flash('Date is required.', 'danger')
            return render_template('record_weight.html', today_date=date.today())
        
        try:
            entry_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Invalid date format. Please use YYYY-MM-DD.', 'danger')
            return render_template('record_weight.html', today_date=date.today(), date_val=date_str, weight_val=weight_str)

        if not weight_str:
            flash('Weight is required.', 'danger')
            return render_template('record_weight.html', today_date=date.today(), date_val=date_str)

        try:
            weight = float(weight_str)
            if weight <= 0:
                flash('Weight must be a positive number.', 'danger')
                return render_template('record_weight.html', today_date=date.today(), date_val=date_str, weight_val=weight_str)
        except ValueError:
            flash('Invalid input for weight. Please enter a number.', 'danger')
            return render_template('record_weight.html', today_date=date.today(), date_val=date_str, weight_val=weight_str)

        new_weight_entry = WeightEntry(
            user_id=current_user.id,
            date=entry_date,
            weight=weight
        )
        db.session.add(new_weight_entry)
        db.session.commit()
        
        # Also update the user's current weight in the User model
        current_user.weight = weight
        db.session.commit()

        flash(f'Weight for {entry_date.strftime("%Y-%m-%d")} recorded successfully!', 'success')
        return redirect(url_for('dashboard', year=entry_date.year, month=entry_date.month))

    return render_template('record_weight.html', today_date=date.today())

def calculate_total_progress(user_id, since_date=None):
    user = User.query.get(user_id)
    if not user:
        return 0

    query = CalorieEntry.query.filter_by(user_id=user_id)
    if since_date:
        query = query.filter(CalorieEntry.date > since_date)
    
    entries = query.all()
    
    total_progress = 0
    
    # Get all exercise entries for the user within the period
    exercise_query = ExerciseEntry.query.filter_by(user_id=user_id)
    if since_date:
        exercise_query = exercise_query.filter(ExerciseEntry.date > since_date)
    all_exercise_entries = exercise_query.all()
    
    # Create a dictionary for quick lookup of exercise calories by date
    exercise_calories_by_date = {}
    for ex_entry in all_exercise_entries:
        exercise_calories_by_date[ex_entry.date] = exercise_calories_by_date.get(ex_entry.date, 0) + ex_entry.calories_burned

    for entry in entries:
        if entry.target_calories_for_day is not None:
            daily_exercise_calories = 0
            if user.exercise_tracking_mode == 'offset':
                daily_exercise_calories = exercise_calories_by_date.get(entry.date, 0)
            
            # Deficit = Target - (Consumed + Adjustments - Exercise (if offset))
            daily_net = entry.target_calories_for_day - \
                        (entry.consumed_calories + entry.adjusted_calories - daily_exercise_calories)
            total_progress += daily_net
            
    return total_progress

@app.route('/set_goal', methods=['GET', 'POST'])
@login_required
def set_goal():
    if request.method == 'POST':
        target_date_str = request.form.get('target_date')
        if not target_date_str:
            flash('Target date is required.', 'danger')
            return redirect(url_for('set_goal'))

        try:
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Invalid date format. Please use YYYY-MM-DD.', 'danger')
            return redirect(url_for('set_goal'))

        if target_date <= date.today():
            flash('Target date must be in the future.', 'danger')
            return redirect(url_for('set_goal'))

        if not current_user.bmr:
            flash('Please complete your initial setup (including BMR) before setting a goal.', 'warning')
            return redirect(url_for('initial_setup'))

        days_to_target = (target_date - date.today()).days
        
        # Calculate TDEE
        if current_user.activity_level:
            tdee = current_user.bmr * current_user.activity_level
        else:
            # If activity_level is None, BMR might have been input directly as TDEE
            tdee = current_user.bmr 

        calories_to_lose_for_1kg = 7200
        calorie_deficit_per_day = calories_to_lose_for_1kg / days_to_target
        
        daily_calorie_goal = round(tdee - calorie_deficit_per_day)

        # Ensure goal is not below a minimum safe level (e.g. 1200 kcal)
        # For this task, we'll just ensure it's positive. A more robust check might be needed.
        if daily_calorie_goal < 0: 
            flash('The calculated daily calorie goal is too low. Please choose a later target date.', 'danger')
            return redirect(url_for('set_goal'))

        current_user.target_date = target_date
        current_user.daily_calorie_goal = daily_calorie_goal
        db.session.commit()

        flash(f'Your goal has been set! Aim for {daily_calorie_goal} kcal/day to lose 1kg by {target_date.strftime("%Y-%m-%d")}.', 'success')
        return redirect(url_for('dashboard'))

    today = date.today()
    min_date = today + timedelta(days=1)
    return render_template('set_goal.html', user=current_user, today=today, min_date=min_date)

@app.route('/initial_setup', methods=['GET', 'POST'])
@login_required
def initial_setup():
    if request.method == 'POST':
        current_user.gender = request.form.get('gender')
        current_user.weight = float(request.form.get('weight'))
        current_user.height = float(request.form.get('height'))
        current_user.age = int(request.form.get('age'))
        
        known_bmr_checkbox = request.form.get('known_bmr_checkbox')
        if known_bmr_checkbox and request.form.get('bmr_direct'):
            current_user.bmr = float(request.form.get('bmr_direct'))
            current_user.activity_level = None # Or set to a default/placeholder if BMR is known
        else:
            current_user.activity_level = float(request.form.get('activity_level'))
            if current_user.gender.lower() == 'male':
                bmr_calculated = (88.362 + (13.397 * current_user.weight) +
                                  (4.799 * current_user.height) - (5.677 * current_user.age))
            else: # Female
                bmr_calculated = (447.593 + (9.247 * current_user.weight) +
                                  (3.098 * current_user.height) - (4.330 * current_user.age))
            current_user.bmr = bmr_calculated

        db.session.commit()
        flash('Your details have been updated successfully!', 'success')
        return redirect(url_for('dashboard'))
    
    # Pre-fill form if data exists
    form_data = {
        'gender': current_user.gender,
        'weight': current_user.weight,
        'height': current_user.height,
        'age': current_user.age,
        'activity_level': current_user.activity_level,
        'bmr_direct': current_user.bmr if current_user.activity_level is None else '' # Show BMR if it was directly inputted
    }
    return render_template('initial_setup.html', form_data=form_data)

class CalorieEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    consumed_calories = db.Column(db.Integer, default=0)
    target_calories_for_day = db.Column(db.Integer, nullable=True)
    adjusted_calories = db.Column(db.Integer, default=0) # For overflow/underflow adjustments

class ExerciseEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    calories_burned = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(200), nullable=True)

class WeightEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    weight = db.Column(db.Float, nullable=False)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
