import os
import sys
import unittest
from datetime import date

# Add project root to sys.path to allow importing app
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from app import app, db, User, WeightEntry, bcrypt

class WeightTrackingTestCase(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        app.config['WTF_CSRF_ENABLED'] = False
        app.config['SECRET_KEY'] = 'test_secret_key' # Ensure a secret key for sessions
        app.config['LOGIN_DISABLED'] = False # Ensure login is not globally disabled for tests
        self.client = app.test_client()

        with app.app_context():
            db.create_all()
            # Create a test user
            hashed_password = bcrypt.generate_password_hash('testpassword').decode('utf-8')
            self.test_user = User(email='test@example.com', password_hash=hashed_password)
            db.session.add(self.test_user)
            db.session.commit()

            # Log in the test user by directly setting the session variable
            # This is a common way to handle login in tests for Flask-Login
            with self.client.session_transaction() as sess:
                sess['_user_id'] = str(self.test_user.id) # Flask-Login uses '_user_id'
                sess['_fresh'] = True # Typically set for fresh logins


    def tearDown(self):
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def test_create_weight_entry_model(self):
        """Test creating a WeightEntry model instance."""
        with app.app_context():
            entry_date = date(2023, 10, 26)
            weight = 70.5
            weight_entry = WeightEntry(user_id=self.test_user.id, date=entry_date, weight=weight)
            db.session.add(weight_entry)
            db.session.commit()

            retrieved_entry = WeightEntry.query.filter_by(user_id=self.test_user.id, date=entry_date).first()
            self.assertIsNotNone(retrieved_entry)
            self.assertEqual(retrieved_entry.weight, weight)
            self.assertEqual(retrieved_entry.user_id, self.test_user.id)

    def test_record_weight_get_requires_login(self):
        """Test GET request to /record_weight requires login."""
        # First, logout by clearing the session
        with self.client.session_transaction() as sess:
            sess.clear()
        
        response = self.client.get('/record_weight', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Login', response.data) # Should be redirected to login page

        # Log back in for subsequent tests if needed (though each test should be independent)
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.test_user.id)
            sess['_fresh'] = True


    def test_record_weight_get_logged_in(self):
        """Test GET request to /record_weight when logged in."""
        response = self.client.get('/record_weight')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Record Your Weight', response.data)
        self.assertIn(b'Weight (kg)', response.data)

    def test_record_weight_post_success(self):
        """Test POST request to /record_weight with valid data."""
        entry_date_str = '2023-10-27'
        weight_kg = 71.0
        
        response = self.client.post('/record_weight', data={
            'date': entry_date_str,
            'weight': str(weight_kg)
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200) 
        self.assertIn(b'Dashboard', response.data) 
        self.assertIn(b'Weight for 2023-10-27 recorded successfully!', response.data)


        with app.app_context():
            entry_date_obj = date(2023, 10, 27)
            weight_entry = WeightEntry.query.filter_by(user_id=self.test_user.id, date=entry_date_obj).first()
            self.assertIsNotNone(weight_entry)
            self.assertEqual(weight_entry.weight, weight_kg)
            
            updated_user = User.query.get(self.test_user.id)
            self.assertEqual(updated_user.weight, weight_kg)

    def test_record_weight_post_invalid_data_missing_weight(self):
        """Test POST request to /record_weight with missing weight."""
        response = self.client.post('/record_weight', data={
            'date': '2023-10-28',
            'weight': '' 
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Weight is required.', response.data)
        self.assertIn(b'Record Your Weight', response.data) # Check form is re-rendered

    def test_record_weight_post_invalid_data_non_numeric_weight(self):
        """Test POST request to /record_weight with non-numeric weight."""
        response = self.client.post('/record_weight', data={
            'date': '2023-10-29',
            'weight': 'abc' 
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Invalid input for weight. Please enter a number.', response.data)
        self.assertIn(b'Record Your Weight', response.data)

    def test_record_weight_post_invalid_data_negative_weight(self):
        """Test POST request to /record_weight with negative weight."""
        response = self.client.post('/record_weight', data={
            'date': '2023-10-30',
            'weight': '-5' 
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Weight must be a positive number.', response.data)
        self.assertIn(b'Record Your Weight', response.data)

    def test_record_weight_post_missing_date(self):
        """Test POST request to /record_weight with missing date."""
        response = self.client.post('/record_weight', data={
            'date': '',
            'weight': '70' 
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Date is required.', response.data)
        self.assertIn(b'Record Your Weight', response.data)


    def test_dashboard_displays_weight_data_in_chart_js(self):
        """Test dashboard displays weight data, checking for JS data presence."""
        with app.app_context():
            # Clear existing entries for the user to ensure clean test
            WeightEntry.query.filter_by(user_id=self.test_user.id).delete()
            db.session.commit()

            # Add specific weight entries for the test
            entry1 = WeightEntry(user_id=self.test_user.id, date=date(2023, 1, 1), weight=75.0)
            entry2 = WeightEntry(user_id=self.test_user.id, date=date(2023, 1, 15), weight=74.5)
            db.session.add_all([entry1, entry2])
            db.session.commit()

        response = self.client.get('/dashboard/') # Added trailing slash
        self.assertEqual(response.status_code, 200)
        
        # Check if the chart canvas is present
        self.assertIn(b'<canvas id="weightChart"></canvas>', response.data)
        
        # Check if the weight_entries data is embedded in the page for Chart.js
        # This looks for the JSON-stringified data that Chart.js would use.
        # Note: The replace call is to handle how Jinja's tojson filter might escape quotes.
        expected_json_part1 = b'const weightEntries = JSON.parse(\'[{"date": "2023-01-01", "weight": 75.0}'
        expected_json_part2 = b'{"date": "2023-01-15", "weight": 74.5}]\');'
        
        response_data_str = response.data.replace(b'&#34;', b"'") # Normalize quotes for easier matching
        
        self.assertIn(expected_json_part1, response_data_str)
        self.assertIn(expected_json_part2, response_data_str)

    def test_dashboard_no_weight_data_message(self):
        """Test dashboard shows 'no data' message for weight chart if no entries."""
        with app.app_context():
            WeightEntry.query.filter_by(user_id=self.test_user.id).delete()
            db.session.commit()

        response = self.client.get('/dashboard/') # Added trailing slash
        self.assertEqual(response.status_code, 200)
        
        # Check that the container for the chart is there
        self.assertIn(b'<div class="chart-wrapper" id="weightChartContainer">', response.data)
        # Check that the JS variable for weight entries is empty
        self.assertIn(b"const weightEntries = JSON.parse('[]');", response.data.replace(b'&#34;', b"'"))
        # Check that the no-data message (which JS would display) is present in the script logic or as a comment.
        # For unit tests, we can't confirm JS execution, but we can check the data passed to the template
        # and the raw HTML structure. The actual message is injected by JS, so we check the condition for it.
        # A more direct check for the text might be brittle if the JS changes how it injects the message.
        # However, the template *does* include the text "No weight data recorded yet" in the JS.
        self.assertIn(b'No weight data recorded yet. Record your weight to see the chart.', response.data)
        # The canvas will still be in the initial HTML, JS removes/hides it.
        # So, we should check it IS there initially.
        self.assertIn(b'<canvas id="weightChart"></canvas>', response.data)


if __name__ == '__main__':
    unittest.main()
