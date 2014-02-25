import sys, time, threading, thread, signal, atexit
import RPi.GPIO as GPIO

# Pibrella Pins, these are BCM
PB_PIN_LIGHT_RED   = 27 # 21 on Rev 1
PB_PIN_LIGHT_AMBER = 17
PB_PIN_LIGHT_GREEN = 4

# Inputs
PB_PIN_INPUT_A = 9
PB_PIN_INPUT_B = 7
PB_PIN_INPUT_C = 8
PB_PIN_INPUT_D = 10

# Outputs
PB_PIN_OUTPUT_A = 22
PB_PIN_OUTPUT_B = 23
PB_PIN_OUTPUT_C = 24
PB_PIN_OUTPUT_D = 25

# Onboard button
PB_PIN_BUTTON = 11

# Onboard buzzer
PB_PIN_BUZZER = 18

# Number of times to udpate
# pulsing LEDs per second
PULSE_FPS = 50
PULSE_FREQUENCY = 100

DEBOUNCE_TIME = 20

# Basic wrapper around Thread to add Event
# for stopping the execution loop and
# exiting cleanly.
class StoppableThread(threading.Thread):
	def __init__(self):
		threading.Thread.__init__(self)
		self.stop_event = threading.Event()
		self.daemon = True         

	def start(self):
		if self.isAlive() == False:
			self.stop_event.clear()
			threading.Thread.start(self)

	def stop(self):
		if self.isAlive() == True:
			# set event to signal thread to terminate
			self.stop_event.set()
			# block calling thread until thread really has terminated
			self.join()

# Basic thread wrapper class for running functions
# asyncronously. Return False from your function
# to abort looping.
class AsyncWorker(StoppableThread):
	def __init__(self, todo):
		StoppableThread.__init__(self)
		self.todo = todo

	def run(self):
		while self.stop_event.is_set() == False:
			if self.todo() == False:
				break


class Pulse(StoppableThread):
	def __init__(self,pin,time_on,time_off,transition_on,transition_off):
		StoppableThread.__init__(self)

		self.pin = pin
		self.time_on = (time_on)
		self.time_off = (time_off)
		self.transition_on = (transition_on)
		self.transition_off = (transition_off)

		self.fps = PULSE_FPS

		# Total time of transition
		self.time_start = time.time()

	def start(self):
		self.time_start = time.time()
		StoppableThread.start(self)

	def run(self):
		# This loop runs at the specified "FPS" uses time.time() 
		while self.stop_event.is_set() == False:
			current_time = time.time() - self.time_start
			delta = current_time % (self.transition_on+self.time_on+self.transition_off+self.time_off)

			if( delta <= self.transition_on ):
				# Transition On Phase
				self.pin.duty_cycle( round(( 100.0 / self.transition_on ) * delta) )

			elif( delta > self.transition_on + self.time_on and delta <= self.transition_on + self.time_on + self.transition_off ):
				# Transition Off Phase
				current_delta = delta - self.transition_on - self.time_on
				self.pin.duty_cycle( round(100.0 - ( ( 100.0 / self.transition_off ) * current_delta )) )

			elif( delta > self.transition_on and delta <= self.transition_on + self.time_on ):
				self.pin.duty_cycle( 100 )

			elif( delta > self.transition_on + self.time_on + self.transition_off ):
				self.pin.duty_cycle( 0 )

			time.sleep(1.0/self.fps) # Pulse framerate

		self.pin.duty_cycle( 0 )

	# This alternative uses tighter loops, but
	# any interruption can cause simultaneously started
	# pulses to desyncronise quite obviously
	def dont_run(self):
		while self.stop_event.is_set() == False:
			if self.transition_on > 0:
				for x in xrange(0, 100):
					if self.stop_event.is_set():
						break
					self.pin.duty_cycle(x)
					time.sleep(float(self.transition_on)/100)
			else:
				self.pin.duty_cycle(100)

			for x in xrange(0, 1000):
				time.sleep(self.time_on/1000)
				if self.stop_event.is_set():
					break

			if self.transition_off > 0:
				for x in reversed(xrange(0, 100)):
					if self.stop_event.is_set():
						break
					self.pin.duty_cycle(x)
					time.sleep(float(self.transition_off)/100)
			else:
				self.pin.duty_cycle(0)

			for x in xrange(0, 1000):
				time.sleep(self.time_off/1000)
				if self.stop_event.is_set():
					break

# Provies an "all" method to Pins collections
class PinIterator:

	def __init__(self):
		self._all = {}
		self._index = []

	# Support accessing with []
	def __getitem__(self, key):
		return self._all[self._index[key]]

	def count(self):
		return len(self._all)

	def _add(self,name,obj):
		# Handle adding additional items after init
		self._all[name] = obj
		self._index.append(name)

	# Catch any calls to self.method_name and
	# hand them off to each class instance
	def __getattr__(self,name):
		def handlerFunction(*args,**kwargs):
			_results = {}
			for node in self._index:
				_results[node] =  getattr(self._all[node],name)(*args)

			# Return the array of results
			return _results

		return handlerFunction

	# Ask for a specific method to be run
	# against all members of the Iterator
	def _do(self,name,*args,**kwargs):
		_results = {}
		for node in self._index:
			handler = getattr(self._all[node],name)
			if hasattr(handler, '__call__'):
				_results[node] = handler(*args)
			else:
				_results[node] = handler
		return _results

class Pins:

	def __getattr__(self,name):
		def handlerFunction(*args,**kwargs):
			return self.all._do(name,*args,**kwargs)
		return handlerFunction

	def __init__(self, **kwargs):
		self.all = PinIterator()
		self._add(**kwargs)

	# Support accessing with [n]
	def __getitem__(self, key):
		return self.all[key]

	def count(self):
		return self.all.count()

	def _add(self, **kwargs):
		for name in kwargs:

			# Make the light available as an attribute of the pins instance
			setattr(self, name, kwargs[name])

			# Add the current light to the iterator for "all"
			self.all._add(name,kwargs[name])

# Pin contains methods that apply
# to both inputs and outputs
class Pin(object):
	type = 'Pin'

	def __init__(self, pin):
		self.pin = pin
		self.last = self.read()
		self.handle_change = False
		self.handle_high = False
		self.handle_low = False

	def has_changed(self):
		if self.read() != self.last:
			self.last = self.read()
			return True
		return False

	def is_off(self):
		return self.read() == 0

	def is_on(self):
		return self.read() == 1

	def read(self):
		return GPIO.input(self.pin)

	def stop(self):
		return True

	is_high = is_on
	is_low = is_off
	get = read

# Input contains methods that
# apply only to inputs
class Input(Pin):

	type = 'Input'

	def __init__(self, pin):
		if self.type == 'Button':
			GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
		else:
			GPIO.setup(pin, GPIO.IN)
		super(Input,self).__init__(pin)

	def on_high(self, callback, bouncetime=DEBOUNCE_TIME):
		def handle_callback(pin):
			callback(self)
		GPIO.add_event_detect(self.pin, GPIO.RISING, callback=handle_callback, bouncetime=bouncetime)

	def on_low(self, callback, bouncetime=DEBOUNCE_TIME):
		def handle_callback(pin):
			callback(self)
		GPIO.add_event_detect(self.pin, GPIO.RISING, callback=handle_callback, bouncetime=bouncetime)
		
	def on_changed(self, callback, bouncetime=DEBOUNCE_TIME):
		def handle_callback(pin):
			callback(self)
		GPIO.add_event_detect(self.pin, GPIO.BOTH, callback=handle_callback, bouncetime=bouncetime)

	def clear_events(self):
		GPIO.remove_event_detect(self.pin)

	# Alias handlers
	changed = on_changed
	pressed = on_high
	released = on_low

class Button(Input):

	type = 'Button'

	def __init__(self, pin):
		super(Button,self).__init__(pin)

	def is_pressed(self):
		return self.is_on()

	def is_released(self):
		return self.is_off()

# Output contains methods that
# apply only to outputs
class Output(Pin):

	type = 'Output'

	def __init__(self, pin):
		#print "Setting " + str(pin) + " to OUTPUT"
		GPIO.setup(pin, GPIO.OUT, initial=0)
		super(Output,self).__init__(pin)
		self.gpio_pwm = GPIO.PWM(pin,1)

	# The crux of an Output is its write function

	def pwm(self,freq,duty_cycle = 50):
		self.gpio_pwm.ChangeDutyCycle(duty_cycle)
		self.gpio_pwm.ChangeFrequency(freq)
		self.gpio_pwm.start(duty_cycle)

	def frequency(self,freq):
		self.gpio_pwm.ChangeFrequency(freq)

	def duty_cycle(self,duty_cycle):
		self.gpio_pwm.ChangeDutyCycle(duty_cycle)

	def stop(self):
		self.gpio_pwm.stop()

	def write(self,value):
		GPIO.output(self.pin,value)

	def on(self):
		self.duty_cycle(100)
		self.gpio_pwm.stop()
		self.write(1)

	def off(self):
		self.duty_cycle(0)
		self.gpio_pwm.stop()
		self.write(0)

	# Alias on/off to conventional names
	high = on
	low  = off

	def toggle(self):
		if( self.read() == 1 ):
			self.write(0)
		else:
			self.write(1)

class Light(Output):

	type = 'Light'

	def __init__(self,pin):
		super(Light,self).__init__(pin)
		self.pulser = Pulse(self,0,0,0,0)
		self.blinking = False
		self.pulsing = False

	def blink(self,on=1,off=-1):
		if off == -1:
			off = on

		off = float(off)
		on = float(on)

		total = off + on

		duty_cycle = 100.0 * (on/total)

		# Stop the thread that's pulsing the LED
		if self.pulsing:
			self.stop_pulse();

		# Use pure PWM blinking, because threads are fugly
		if self.blinking:
			self.frequency(1.0/total)
			self.duty_cycle(duty_cycle)
		else:
			self.pwm(1.0/total,duty_cycle)
			self.blinking = True

	def pulse(self,time_on=-1,time_off=-1,transition_on=-1,transition_off=-1):
		# This needs a thread to handle the fade in and out

		if time_off == -1:
			time_off = time_on

		if transition_off == -1 and transition_on > -1:
			transition_off = transition_on

		if transition_on == -1:
			transition_on = time_on
			transition_off = time_on

		# Fire up PWM if it's not running
		if self.blinking == False:
			self.pwm(PULSE_FREQUENCY,0.0)

		# pulse(x,y,0,0) is basically just a regular blink
		# only fire up a thread if we really need it
		if transition_on == 0 and transition_off == 0:
			self.blink(time_on,time_off)
		else:
			self.pulser.time_on = time_on
			self.pulser.time_off = time_off
			self.pulser.transition_on = transition_on
			self.pulser.transition_off = transition_off
			self.pulser.start() # Kick off the pulse thread
			self.pulsing = True

		self.blinking = True

	def on(self):
		# Some gymnastics here to fix a big ( in RPi.GPIO?)
		# That occurs when trying to output(1) immediately
		# after stopping the PWM
		blinking = self.blinking
		self.stop()
		# A small delay is needed. Ugly, but it works
		if blinking:
			time.sleep(0.05)
		return super(Light,self).on()
	high = on

	def off(self):
		# Obviously stop blinking and/or pulsing if we're
		# turning this light off
		self.stop()
		return super(Light,self).off()
	low = off

	def stop_pulse(self):
		self.pulsing = False
		self.pulser.stop()
		self.pulser = Pulse(self,0,0,0,0)

	def stop(self):
		self.blinking = False
		self.stop_pulse()

		# Abruptly stopping PWM is a bad idea
		# unless we're writing a 1 or 0
		# So don't inherit the parent classes
		# stop() since weird bugs happen

		# Threaded PWM access was aborting with
		# no errors when stop coincided with a
		# duty cycle change.

		#time.sleep(0.001)
		#super(Light,self).stop()

class Buzzer(Output):

	type = 'Buzzer'

	def buzz(self,frequency):
		self.pwm(frequency,30)

	# Play a single note, mathmatically
	# deduced from its index, offset from 440Hz
	def note(self,note):
		note = float(note)
		a = pow(2.0, 1.0/12.0)
		self.buzz(440.00 * pow(a,note))

	# Example sound effects
	def success(self):
		def success():
			for note in xrange(0,4):
				self.note(note)
				time.sleep(0.2)
			time.sleep(0.4)
			self.stop()
			# Prevent looping
			return False
		async = AsyncWorker(success)
		async.start()

	def fail(self):
		def fail():
			for note in reversed(xrange(0,5)):
				self.note(note)
				time.sleep(0.2)
			time.sleep(0.4)
			self.stop()
			# Prevent looping
			return False
		async = AsyncWorker(fail)
		async.start()

class Pibrella:
	light = None
	input = None
	output = None
	button = None
	buzzer = None
	pin = None
	running = False
	workers = {}

	def async_start(self,name,function):
		self.workers[name] = AsyncWorker(function)
		self.workers[name].start()

	def async_stop(self,name):
		self.workers[name].stop()

	def async_stop_all(self):
		for worker in self.workers:
			print "Stopping user task: " + worker
			self.workers[worker].stop()

	def set_timeout(self,function,seconds):
		def fn_timeout():
			time.sleep(seconds)
			function()
			return False
		timeout = AsyncWorker(fn_timeout)
		timeout.start()

	# Should this ever be exposed to the user?
	def pause(self):
		signal.pause()

	# Register a loop to run
	def loop(self, callback):
		self.running = True
		while self.running:
			callback()

	# Stop a running loop
	def stop(self):
		self.running = False

	# Exit cleanly
	def exit(self):
		print "\nPibrella exiting cleanly, please wait..."

		print "Stopping flashy things..."
		self.pin.all.stop()

		print "Stopping user tasks..."
		self.async_stop_all()

		print "Cleaning up..."
		GPIO.cleanup()

		print "Goodbye!"

# Set mode to use BCM pin numberings
# TODO: Probably want to change this to board?
GPIO.setmode(GPIO.BCM)
#GPIO.setwarnings(False)

# Catch exit and make sure we clean up
# before classes are too destroyed to do so
atexit.register(lambda: pibrella.exit())

# Instance our Pibrella class
pibrella = Pibrella()

# Create an object containing our lights
pibrella.light = Pins()
pibrella.light._add(red = Light(PB_PIN_LIGHT_RED))
pibrella.light._add(amber = Light(PB_PIN_LIGHT_AMBER))
pibrella.light._add(green = Light(PB_PIN_LIGHT_GREEN))

# Create an object containing our inputs
pibrella.input = Pins()
pibrella.input._add(a = Input(PB_PIN_INPUT_A))
pibrella.input._add(b = Input(PB_PIN_INPUT_B))
pibrella.input._add(c = Input(PB_PIN_INPUT_C))
pibrella.input._add(d = Input(PB_PIN_INPUT_D))

# Create an object contianing our outputs
pibrella.output = Pins()
pibrella.output._add(e = Output(PB_PIN_OUTPUT_A))
pibrella.output._add(f = Output(PB_PIN_OUTPUT_B))
pibrella.output._add(g = Output(PB_PIN_OUTPUT_C))
pibrella.output._add(h = Output(PB_PIN_OUTPUT_D))

# And our button
pibrella.button = Button(PB_PIN_BUTTON)

# And our buzzer
pibrella.buzzer = Buzzer(PB_PIN_BUZZER)

# And collect everything into one place
pibrella.pin = Pins()

# Outputs
pibrella.pin._add(e = pibrella.output.e)
pibrella.pin._add(f = pibrella.output.f)
pibrella.pin._add(g = pibrella.output.g)
pibrella.pin._add(h = pibrella.output.h)

# Inputs
pibrella.pin._add(a  = pibrella.input.a)
pibrella.pin._add(b  = pibrella.input.b)
pibrella.pin._add(c  = pibrella.input.c)
pibrella.pin._add(d  = pibrella.input.d)

# Lights
pibrella.pin._add(red   = pibrella.light.red)
pibrella.pin._add(amber = pibrella.light.amber)
pibrella.pin._add(green = pibrella.light.green)

# Button
pibrella.pin._add(button = pibrella.button)

# Fire up our worker
# pibrellaworker._setup(pibrella)

# Alias all the things!
# This lets users "import pibrella" instead of "import pibrella from pibrella"
pause = pibrella.pause

# Lets you register a function to loop
loop = pibrella.loop

# Stops the loop
stop = pibrella.stop

# IO
light = pibrella.light
input = pibrella.input
output = pibrella.output
button = pibrella.button
buzzer = pibrella.buzzer
pin = pibrella.pin

# Aliases of input/output and light
In = IN = input
Out = OUT = output
Led = LED = light

# Advanced: Asyncronous function execution
async_start = pibrella.async_start
async_stop = pibrella.async_stop
async_stop_all = pibrella.async_stop_all

set_timeout = pibrella.set_timeout