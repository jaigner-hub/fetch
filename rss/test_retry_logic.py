#!/usr/bin/env python
"""
Test script to verify retry logic for API calls
"""
import os
import sys
import django
import time

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rss.settings')
django.setup()

from feeds.article_analyzer import retry_on_overload
import logging

# Setup logging to see retry attempts
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Simulate an API that's overloaded
class SimulatedAPI:
    def __init__(self, fail_count=5):
        self.attempts = 0
        self.fail_count = fail_count
    
    @retry_on_overload(max_retries=15, initial_delay=1, backoff_factor=1.3, max_delay=10)
    def call_api(self):
        self.attempts += 1
        logger.info(f"API call attempt #{self.attempts}")
        
        if self.attempts <= self.fail_count:
            logger.error(f"Simulating 529 overload error (attempt {self.attempts}/{self.fail_count})")
            raise Exception("Error code: 529 - Service overloaded")
        
        logger.success = logger.info
        logger.success(f"SUCCESS! API call succeeded on attempt #{self.attempts}")
        return {"success": True, "data": "API response"}

def test_retry_logic():
    """Test the retry logic with different failure scenarios"""
    
    print("\n" + "="*60)
    print("Testing retry logic with simulated overload errors")
    print("="*60 + "\n")
    
    # Test 1: Success after 3 failures
    print("Test 1: API succeeds after 3 failures")
    print("-" * 40)
    api1 = SimulatedAPI(fail_count=3)
    try:
        start_time = time.time()
        result = api1.call_api()
        elapsed = time.time() - start_time
        print(f"✓ Success after {api1.attempts} attempts in {elapsed:.1f} seconds")
        print(f"  Result: {result}")
    except Exception as e:
        print(f"✗ Failed: {e}")
    
    print("\n")
    
    # Test 2: Success after 8 failures  
    print("Test 2: API succeeds after 8 failures")
    print("-" * 40)
    api2 = SimulatedAPI(fail_count=8)
    try:
        start_time = time.time()
        result = api2.call_api()
        elapsed = time.time() - start_time
        print(f"✓ Success after {api2.attempts} attempts in {elapsed:.1f} seconds")
        print(f"  Result: {result}")
    except Exception as e:
        print(f"✗ Failed: {e}")
    
    print("\n")
    
    # Test 3: Failure - too many retries needed
    print("Test 3: API needs more retries than allowed (20 failures)")
    print("-" * 40)
    api3 = SimulatedAPI(fail_count=20)
    try:
        start_time = time.time()
        result = api3.call_api()
        elapsed = time.time() - start_time
        print(f"✓ Success after {api3.attempts} attempts in {elapsed:.1f} seconds")
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"✗ Expected failure after {api3.attempts} attempts in {elapsed:.1f} seconds")
        print(f"  Error: {e}")
    
    print("\n" + "="*60)
    print("Retry logic test complete!")
    print("="*60)

if __name__ == "__main__":
    test_retry_logic()