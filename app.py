from flask import Flask, render_template, request, redirect, url_for, flash
from datetime import datetime, date, timedelta
import calendar
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

            if entry:
                consumed_for_day = entry.consumed_calories
                adjusted_for_day = entry.adjusted_calories
                # If entry has its own target, it overrides the goal's general target for that day
                if entry.target_calories_for_day is not None:
                    target_for_day = entry.target_calories_for_day
                # If entry exists but target_for_day is still None (e.g. created outside goal),
                # but this day IS in a goal period, set it.
                elif is_within_goal_period and day_date_obj.month == month:
                     # We don't persist this back to entry from here, record_calories will handle it if visited
                     pass # target_for_day is already user.daily_calorie_goal from above

            effective_consumed = consumed_for_day + adjusted_for_day

            week_data.append({
                'date': day_date_obj, # datetime.date object
                'day_number': day_date_obj.day,
                'is_current_month': day_date_obj.month == month,
                'consumed_calories': consumed_for_day, # Actual intake
                'adjusted_calories': adjusted_for_day, # Adjustments from other days
                'effective_consumed': effective_consumed, # Total for display against target
                'target_calories_for_day': target_for_day
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


    return render_template('dashboard.html', user=user, calendar_data=calendar_data, 
                           cal_year=year, cal_month=month,
                           prev_year=prev_year, prev_month=prev_month,
                           next_year=next_year, next_month=next_month,
                           total_deficit_since_last_reset=total_deficit_since_last_reset,
                           cycle_complete_flag=cycle_complete_flag,
                           total_exercise_kcal_since_reset=total_exercise_kcal_since_reset)


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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
